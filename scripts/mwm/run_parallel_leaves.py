#!/usr/bin/env python3
"""Run CoMaps-release leaf processing with separate download/process pools.

Downloads: polite concurrency (default 2).
Processing: configurable; default from free RAM (see leaf_scheduler).

Leaves are ordered Nordic → Europe → World. Resume via processed.json.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Empty, Queue

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))
from comaps_release import LeafInfo, download_leaf  # noqa: E402
from leaf_scheduler import (  # noqa: E402
    load_processed,
    pending_leaves,
    resolve_download_concurrency,
    resolve_process_concurrency,
)


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
    proc_n = args.process_concurrency or resolve_process_concurrency()
    dl_n = args.download_concurrency or resolve_download_concurrency()
    print(
        json.dumps(
            {
                "pending": len(leaves),
                "process_concurrency": proc_n,
                "download_concurrency": dl_n,
                "first_leaves": leaves[:8],
            }
        ),
        flush=True,
    )
    if not leaves:
        print(json.dumps({"done": True, "processed": 0}))
        return 0

    python = os.environ.get("NAVI_MWM_PYTHON", sys.executable)
    download_dir = args.work / "tmp" / "_downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    ready: Queue = Queue(maxsize=max(proc_n * 2, 4))
    failures: list[str] = []
    ok = 0
    t0 = time.monotonic()

    def download_one(leaf: str) -> Path:
        meta = discovery["leaves"][leaf]
        dest = download_dir / f"{leaf}.mwm"
        if dest.is_file() and dest.stat().st_size == int(meta["size"]):
            return dest
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
        with ThreadPoolExecutor(max_workers=dl_n, thread_name_prefix="mwm-dl") as pool:
            futs = {pool.submit(download_one, leaf): leaf for leaf in leaves}
            for fut in as_completed(futs):
                leaf = futs[fut]
                try:
                    path = fut.result()
                    ready.put(("ok", leaf, path))
                except Exception as e:  # noqa: BLE001
                    ready.put(("err", leaf, e))
        ready.put(("done", None, None))

    def process_one(leaf: str, mwm_path: Path) -> tuple[str, int, str]:
        env = os.environ.copy()
        env["NAVI_MWM_PREFETCH_MWM"] = str(mwm_path)
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
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        out = (r.stdout or "") + (r.stderr or "")
        for p in (mwm_path, Path(str(mwm_path) + ".part")):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        return leaf, r.returncode, out

    dl_thread = threading.Thread(target=downloader, name="mwm-dl-sched", daemon=True)
    dl_thread.start()

    with ThreadPoolExecutor(max_workers=proc_n, thread_name_prefix="mwm-proc") as pool:
        futs = []
        downloads_done = False
        while not downloads_done or futs:
            # Drain ready queue
            try:
                while True:
                    status, leaf, payload = ready.get_nowait()
                    if status == "done":
                        downloads_done = True
                    elif status == "err":
                        failures.append(leaf)
                        print(
                            json.dumps({"leaf": leaf, "ok": False, "error": str(payload)}),
                            flush=True,
                        )
                    else:
                        futs.append(pool.submit(process_one, leaf, payload))
            except Empty:
                pass

            # Collect finished processors without blocking forever
            still = []
            for fut in futs:
                if fut.done():
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
            if not downloads_done or futs:
                time.sleep(0.2)

    dl_thread.join()
    # Aggregate Mapterhorn tile cache hit rate across elevation stats.
    hits = misses = 0
    stats_dir = args.work / "stats"
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
        "wall_s": round(time.monotonic() - t0, 1),
        "process_concurrency": proc_n,
        "download_concurrency": dl_n,
        "tile_cache_hits": hits,
        "tile_cache_misses": misses,
        "tile_cache_hit_rate": (hits / lookups) if lookups else None,
    }
    print(json.dumps(summary), flush=True)
    (args.work / "meta" / "run-summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
