#!/usr/bin/env python3
"""Run CoMaps-release leaf processing with separate download/process pools.

Downloads: polite concurrency (default 2) bounded by a configurable prefetch disk
budget (default 20 GiB).
Processing: configurable; default derived from free RAM + per-stage peaks with
a separate wetland annotate semaphore (default 2).
Cache eviction: initialized once and evicted solely by the parent process.
Schedule guard: pauses automatically around weekly .rkyv bake (default 6h
before Monday 00:00 UTC until bake finished or pause window ends).

Leaves are ordered Nordic -> Europe -> World. Resume via processed.json.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from queue import Empty, Full, Queue

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))
from comaps_release import LeafInfo, download_leaf  # noqa: E402
from leaf_scheduler import (  # noqa: E402
    evaluate_schedule_guard,
    load_processed,
    load_schedule_guard_config,
    pending_leaves,
    resolve_annotate_concurrency,
    resolve_download_concurrency,
    resolve_prefetch_disk_budget_bytes,
    resolve_process_concurrency,
)


def _load_elev_module():
    path = SCRIPT_DIR / "build-elevation-overrides.py"
    spec = importlib.util.spec_from_file_location("build_elevation_overrides", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class DownloadBudget:
    """Thread-safe disk budget tracker for prefetched/in-flight MWMs."""

    def __init__(self, budget_bytes: int, initial_used_bytes: int = 0) -> None:
        self.budget_bytes = max(0, int(budget_bytes))
        self.used_bytes = max(0, int(initial_used_bytes))
        self._cond = threading.Condition()

    def acquire(
        self,
        nbytes: int,
        *,
        stop_event: threading.Event | None = None,
        pause_event: threading.Event | None = None,
        poll_s: float = 0.25,
    ) -> bool:
        need = max(0, int(nbytes))
        with self._cond:
            while True:
                if stop_event is not None and stop_event.is_set():
                    return False
                paused = pause_event is not None and pause_event.is_set()
                if not paused:
                    if self.budget_bytes <= 0:
                        self.used_bytes += need
                        return True
                    if self.used_bytes + need <= self.budget_bytes or self.used_bytes == 0:
                        self.used_bytes += need
                        return True
                self._cond.wait(timeout=poll_s)

    def release(self, nbytes: int) -> None:
        rel = max(0, int(nbytes))
        with self._cond:
            self.used_bytes = max(0, self.used_bytes - rel)
            self._cond.notify_all()

    def notify(self) -> None:
        with self._cond:
            self._cond.notify_all()


def _dir_bytes(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return 0
    for entry in os.scandir(path):
        try:
            if entry.is_file(follow_symlinks=False):
                total += entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--discovery", type=Path, required=True)
    ap.add_argument("--dump-bin", type=Path, required=True)
    ap.add_argument("--sidecar-bin", type=Path, required=True)
    ap.add_argument("--resources-path", type=Path, required=True)
    ap.add_argument("--attribution", type=Path, required=True)
    ap.add_argument("--leaf", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--skip-annotate", action="store_true")
    ap.add_argument("--skip-elevation", action="store_true")
    ap.add_argument("--elev-zoom", type=int, default=0)
    ap.add_argument("--process-concurrency", type=int, default=0)
    ap.add_argument("--download-concurrency", type=int, default=0)
    ap.add_argument("--annotate-concurrency", type=int, default=0)
    ap.add_argument("--prefetch-budget-gib", type=float, default=-1.0)
    ap.add_argument("--stop-file", type=Path, default=None)
    args = ap.parse_args()

    discovery = json.loads(args.discovery.read_text())
    state_path = args.work / "meta" / "processed.json"
    processed = load_processed(state_path)
    leaves = pending_leaves(
        discovery["leaves"].keys(),
        processed,
        leaf_filter=args.leaf or None,
        limit=args.limit,
    )
    stats_dir = args.work / "stats"
    proc_n = args.process_concurrency or resolve_process_concurrency(stats_dir=stats_dir)
    dl_n = args.download_concurrency or resolve_download_concurrency()
    ann_n = args.annotate_concurrency or resolve_annotate_concurrency()
    budget_bytes = (
        max(0, int(args.prefetch_budget_gib * (1024**3)))
        if args.prefetch_budget_gib >= 0
        else resolve_prefetch_disk_budget_bytes()
    )
    stop_file = args.stop_file or (args.work / "meta" / "stop.requested")
    guard_cfg = load_schedule_guard_config()

    print(
        json.dumps(
            {
                "pending": len(leaves),
                "process_concurrency": proc_n,
                "download_concurrency": dl_n,
                "annotate_concurrency": ann_n,
                "prefetch_budget_gib": round(budget_bytes / (1024**3), 2),
                "schedule_guard": guard_cfg.enabled,
                "first_leaves": leaves[:8],
            }
        ),
        flush=True,
    )
    if not leaves:
        print(json.dumps({"done": True, "processed": 0}))
        return 0

    # Initialize Mapterhorn cache size once in the parent and run LRU eviction here only.
    elev_mod = None
    cache_dir: Path | None = None
    cache_max_bytes = int(
        os.environ.get("NAVI_MWM_MAPTERHORN_CACHE_MAX_BYTES", "58755104232")
        or "58755104232"
    )
    if not args.skip_elevation and os.environ.get("NAVI_MWM_MAPTERHORN_CACHE", "").strip():
        cache_dir = Path(os.environ["NAVI_MWM_MAPTERHORN_CACHE"])
        elev_mod = _load_elev_module()
        ts0 = time.monotonic()
        init_bytes = elev_mod.init_cache_size(cache_dir, force_scan=True)
        print(
            json.dumps(
                {
                    "cache_init_bytes": init_bytes,
                    "cache_max_bytes": cache_max_bytes,
                    "cache_scan_s": round(time.monotonic() - ts0, 2),
                }
            ),
            flush=True,
        )

    python = os.environ.get("NAVI_MWM_PYTHON", sys.executable)
    download_dir = args.work / "tmp" / "_downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    initial_dl_bytes = _dir_bytes(download_dir)
    budget = DownloadBudget(budget_bytes, initial_used_bytes=initial_dl_bytes)
    ready: Queue = Queue(maxsize=max(proc_n * 2, 4))
    failures: list[str] = []
    ok = 0
    t0 = time.monotonic()

    stop_event = threading.Event()
    pause_event = threading.Event()
    stop_reason: dict[str, str] = {"reason": ""}

    def _request_stop(reason: str) -> None:
        if not stop_event.is_set():
            stop_reason["reason"] = reason
            stop_event.set()
            budget.notify()
            print(json.dumps({"graceful_stop": reason}), flush=True)

    def _sig_handler(signum, _frame) -> None:
        _request_stop(f"signal:{signum}")

    prev_sigterm = signal.getsignal(signal.SIGTERM)
    prev_sigint = signal.getsignal(signal.SIGINT)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _sig_handler)
        signal.signal(signal.SIGINT, _sig_handler)

    def _put_ready(item) -> bool:
        while not stop_event.is_set():
            try:
                ready.put(item, timeout=0.25)
                return True
            except Full:
                continue
        return False

    def download_one(leaf: str, dest: Path) -> Path:
        meta = discovery["leaves"][leaf]
        download_leaf(
            mirrors=discovery["mirrors"],
            map_series=discovery["map_series"],
            version=int(discovery["version"]),
            leaf=LeafInfo(
                id=leaf,
                size=int(meta["size"]),
                sha1_base64=str(meta["sha1_base64"]),
                affiliations=list(meta.get("affiliations") or []),
            ),
            dest=dest,
        )
        return dest

    def downloader() -> None:
        active: dict[Future, tuple[str, int]] = {}
        with ThreadPoolExecutor(max_workers=dl_n, thread_name_prefix="mwm-dl") as pool:
            idx = 0
            while idx < len(leaves) and not stop_event.is_set():
                # Drain any completed futures first
                done_futs = [f for f in active if f.done()]
                for fut in done_futs:
                    leaf_id, reserved = active.pop(fut)
                    try:
                        path = fut.result()
                        if not _put_ready(("ok", leaf_id, path, reserved)):
                            break
                    except Exception as e:  # noqa: BLE001
                        budget.release(reserved)
                        if not _put_ready(("err", leaf_id, e, 0)):
                            break

                if len(active) >= dl_n:
                    wait(active.keys(), timeout=0.25, return_when=FIRST_COMPLETED)
                    continue

                leaf = leaves[idx]
                meta = discovery["leaves"][leaf]
                expected_size = int(meta["size"])
                dest = download_dir / f"{leaf}.mwm"
                if dest.is_file() and dest.stat().st_size == expected_size:
                    idx += 1
                    if not _put_ready(("ok", leaf, dest, expected_size)):
                        break
                    continue

                part = dest.with_suffix(dest.suffix + ".part")
                existing_part = part.stat().st_size if part.is_file() else 0
                needed = max(0, expected_size - existing_part)
                if not budget.acquire(
                    needed,
                    stop_event=stop_event,
                    pause_event=pause_event,
                    poll_s=0.25,
                ):
                    break
                idx += 1
                fut = pool.submit(download_one, leaf, dest)
                active[fut] = (leaf, expected_size)

            # Drain remaining in-flight downloads
            while active and not stop_event.is_set():
                done, _ = wait(active.keys(), timeout=0.25, return_when=FIRST_COMPLETED)
                for fut in done:
                    leaf_id, reserved = active.pop(fut)
                    try:
                        path = fut.result()
                        _put_ready(("ok", leaf_id, path, reserved))
                    except Exception as e:  # noqa: BLE001
                        budget.release(reserved)
                        _put_ready(("err", leaf_id, e, 0))

        if not stop_event.is_set():
            _put_ready(("done", None, None, 0))

    annotate_lock_dir = args.work / "meta" / "annotate-locks"

    def process_one(leaf: str, mwm_path: Path, reserved_bytes: int) -> tuple[str, int, str]:
        env = os.environ.copy()
        env["NAVI_MWM_PREFETCH_MWM"] = str(mwm_path)
        env["NAVI_MWM_ANNOTATE_CONCURRENCY"] = str(ann_n)
        env["NAVI_MWM_ANNOTATE_LOCK_DIR"] = str(annotate_lock_dir)
        env["NAVI_MWM_CACHE_PARENT_EVICT"] = "1"
        cmd = [
            python,
            str(SCRIPT_DIR / "process_comaps_leaf.py"),
            "--leaf",
            leaf,
            "--work",
            str(args.work),
            "--discovery",
            str(args.discovery),
            "--dump-bin",
            str(args.dump_bin),
            "--sidecar-bin",
            str(args.sidecar_bin),
            "--resources-path",
            str(args.resources_path),
            "--attribution",
            str(args.attribution),
        ]
        if args.skip_annotate:
            cmd.append("--skip-annotate")
        if args.skip_elevation:
            cmd.append("--skip-elevation")
        if args.elev_zoom > 0:
            cmd.extend(["--elev-zoom", str(args.elev_zoom)])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, env=env)
            out = (r.stdout or "") + (r.stderr or "")
            return leaf, r.returncode, out
        finally:
            released = 0
            for p in (mwm_path, Path(str(mwm_path) + ".part")):
                try:
                    if p.is_file():
                        released += p.stat().st_size
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            budget.release(max(released, reserved_bytes))

    dl_thread = threading.Thread(target=downloader, name="mwm-dl-sched", daemon=True)
    dl_thread.start()

    try:
        with ThreadPoolExecutor(max_workers=proc_n, thread_name_prefix="mwm-proc") as pool:
            futs: list[Future] = []
            downloads_done = False
            guard_paused = False
            last_guard_check = 0.0

            while True:
                if stop_file.is_file():
                    _request_stop(f"stop_file:{stop_file}")

                now_mono = time.monotonic()
                if now_mono - last_guard_check >= 5.0:
                    last_guard_check = now_mono
                    should_pause, reason, resume_at = evaluate_schedule_guard(cfg=guard_cfg)
                    if should_pause and not guard_paused:
                        guard_paused = True
                        pause_event.set()
                        print(
                            json.dumps(
                                {
                                    "schedule_guard": "paused",
                                    "reason": reason,
                                    "resume_at_utc": resume_at.isoformat() if resume_at else None,
                                    "in_flight": len(futs),
                                }
                            ),
                            flush=True,
                        )
                    elif not should_pause and guard_paused:
                        guard_paused = False
                        pause_event.clear()
                        budget.notify()
                        print(
                            json.dumps({"schedule_guard": "resumed", "reason": reason}),
                            flush=True,
                        )

                # Drain ready queue only when not paused, not stopping, and below worker cap
                if not guard_paused and not stop_event.is_set():
                    while len(futs) < proc_n:
                        try:
                            status, leaf, payload, reserved = ready.get_nowait()
                        except Empty:
                            break
                        if status == "done":
                            downloads_done = True
                            break
                        if status == "err":
                            failures.append(leaf)
                            print(
                                json.dumps({"leaf": leaf, "ok": False, "error": str(payload)}),
                                flush=True,
                            )
                        else:
                            futs.append(pool.submit(process_one, leaf, payload, reserved))

                # Collect finished processors
                still: list[Future] = []
                finished_any = False
                for fut in futs:
                    if fut.done():
                        finished_any = True
                        leaf, rc, out = fut.result()
                        sys.stdout.write(out)
                        sys.stdout.flush()
                        if rc == 0:
                            ok += 1
                        else:
                            failures.append(leaf)
                    else:
                        still.append(fut)
                futs = still

                # Single-owner cache eviction in parent after leaves complete
                if finished_any and elev_mod is not None and cache_dir is not None:
                    final_b, ev_cnt, ev_b = elev_mod.evict_cache_lru(cache_dir, cache_max_bytes)
                    if ev_cnt > 0:
                        print(
                            json.dumps(
                                {
                                    "cache_evicted": ev_cnt,
                                    "evicted_bytes": ev_b,
                                    "cache_bytes": final_b,
                                }
                            ),
                            flush=True,
                        )

                if stop_event.is_set() and not futs:
                    break
                if downloads_done and not futs:
                    break

                time.sleep(0.5 if guard_paused else 0.2)
    finally:
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, prev_sigterm)
            signal.signal(signal.SIGINT, prev_sigint)

    dl_thread.join(timeout=5.0)

    if stop_event.is_set() and stop_file.is_file():
        try:
            stop_file.unlink()
        except OSError:
            pass

    # Aggregate Mapterhorn tile cache hit rate across elevation stats.
    hits = misses = 0
    if stats_dir.is_dir():
        for p in stats_dir.glob("*.elevation.json"):
            try:
                s = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            hits += int(s.get("tile_cache_hits") or 0)
            misses += int(s.get("tile_cache_misses") or 0)
    lookups = hits + misses
    summary = {
        "ok": ok,
        "failed": failures,
        "stopped_early": stop_event.is_set(),
        "stop_reason": stop_reason["reason"] or None,
        "wall_s": round(time.monotonic() - t0, 1),
        "process_concurrency": proc_n,
        "download_concurrency": dl_n,
        "annotate_concurrency": ann_n,
        "prefetch_budget_gib": round(budget_bytes / (1024**3), 2),
        "tile_cache_hits": hits,
        "tile_cache_misses": misses,
        "tile_cache_hit_rate": (hits / lookups) if lookups else None,
    }
    print(json.dumps(summary), flush=True)
    (args.work / "meta" / "run-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
