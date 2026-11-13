#!/usr/bin/env python3
"""Process one CoMaps leaf into a Navi sidecar (download -> dump -> annotate -> elev -> bake).

Designed for parallel invocation. Updates processed.json atomically under a
dedicated lock file. Uses a flock slot pool to bound concurrent wetland annotate
stages. Records per-stage wall time and peak RSS. Deletes the MWM and
intermediate JSONL after success.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import resource
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))
from comaps_release import LeafInfo, download_leaf  # noqa: E402
from leaf_scheduler import (  # noqa: E402
    DEFAULT_ANNOTATE_CONCURRENCY,
    load_processed,
    save_processed_atomic,
)


def sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def record_processed(
    state_path: Path,
    leaf: str,
    mwm: Path,
    out: Path,
    discovery: dict,
    *,
    wall_s: float | None = None,
) -> float:
    """Hash MWM + sidecar and atomically update processed.json under a dedicated lockfile.

    Returns hashing + state-update wall seconds.
    """
    t0 = time.monotonic()
    meta = discovery["leaves"][leaf]
    mwm_sha, mwm_n = sha256_file(mwm)
    sc_sha, sc_n = sha256_file(out)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(".lock")
    with lock_path.open("a+") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            st = load_processed(state_path)
            rec = {
                "comaps_bytes": meta["size"],
                "comaps_sha1_base64": meta["sha1_base64"],
                "mwm_bytes": mwm_n,
                "mwm_sha256": mwm_sha,
                "sidecar_bytes": sc_n,
                "sidecar_sha256": sc_sha,
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            if wall_s is not None:
                rec["wall_s"] = round(wall_s, 1)
            st.setdefault("leaves", {})[leaf] = rec
            st["release_version"] = discovery["version"]
            save_processed_atomic(state_path, st)
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
    return time.monotonic() - t0


@contextlib.contextmanager
def acquire_annotate_slot(
    lock_dir: Path,
    max_slots: int,
    *,
    poll_s: float = 0.05,
):
    """Cross-process semaphore using flock over N slot lockfiles.

    Yields seconds spent waiting for a slot.
    """
    slots = max(1, int(max_slots))
    lock_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    acquired_fh = None
    try:
        while True:
            for idx in range(slots):
                p = lock_dir / f"slot-{idx}.lock"
                fh = p.open("a+")
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired_fh = fh
                    break
                except BlockingIOError:
                    fh.close()
            if acquired_fh is not None:
                break
            time.sleep(poll_s)
        yield time.monotonic() - t0
    finally:
        if acquired_fh is not None:
            try:
                fcntl.flock(acquired_fh.fileno(), fcntl.LOCK_UN)
            finally:
                acquired_fh.close()


def _run_stage(cmd: list[str], *, env: dict[str, str] | None = None) -> tuple[str, str, int, float]:
    """Run a child command and return (stdout, stderr, peak_rss_kib, wall_s)."""
    t0 = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    out_chunks: list[str] = []
    err_chunks: list[str] = []

    def _drain(stream, bucket: list[str]) -> None:
        if stream is None:
            return
        try:
            bucket.append(stream.read())
        finally:
            stream.close()

    t_out = threading.Thread(target=_drain, args=(proc.stdout, out_chunks), daemon=True)
    t_err = threading.Thread(target=_drain, args=(proc.stderr, err_chunks), daemon=True)
    t_out.start()
    t_err.start()
    _, status, ru = os.wait4(proc.pid, 0)
    t_out.join()
    t_err.join()
    wall_s = time.monotonic() - t0
    rc = os.waitstatus_to_exitcode(status)
    stdout = "".join(out_chunks)
    stderr = "".join(err_chunks)
    if rc != 0:
        raise subprocess.CalledProcessError(rc, cmd, output=stdout, stderr=stderr)
    return stdout, stderr, int(ru.ru_maxrss), wall_s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leaf", required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--discovery", type=Path, required=True)
    ap.add_argument("--dump-bin", type=Path, required=True)
    ap.add_argument("--sidecar-bin", type=Path, required=True)
    ap.add_argument("--resources-path", type=Path, required=True)
    ap.add_argument("--attribution", type=Path, required=True)
    ap.add_argument("--skip-annotate", action="store_true")
    ap.add_argument("--skip-elevation", action="store_true")
    ap.add_argument("--elev-zoom", type=int, default=0)
    args = ap.parse_args()

    discovery = json.loads(args.discovery.read_text())
    leaf = args.leaf
    if leaf not in discovery["leaves"]:
        raise SystemExit(f"unknown leaf {leaf}")
    meta = discovery["leaves"][leaf]
    state_path = args.work / "meta" / "processed.json"

    # Skip if already done
    st = load_processed(state_path)
    if leaf in st.get("leaves", {}):
        print(json.dumps({"leaf": leaf, "skipped": True}))
        return 0

    tmp = args.work / "tmp" / leaf.replace("/", "_")
    tmp.mkdir(parents=True, exist_ok=True)
    mwm = tmp / f"{leaf}.mwm"
    roads = tmp / f"{leaf}.roads.jsonl"
    wetlands = tmp / f"{leaf}.wetlands.jsonl"
    wjson = tmp / f"{leaf}.wetland.jsonl"
    bjson = tmp / f"{leaf}.boardwalk.jsonl"
    ejson = tmp / f"{leaf}.elevation.jsonl"
    out = args.work / "sidecars" / f"{leaf}.navi-sidecar.v1.zst"
    out_tmp = out.with_name(f".{out.name}.tmp.{os.getpid()}")
    estat = args.work / "stats" / f"{leaf}.elevation.json"
    wstat = args.work / "stats" / f"{leaf}.wetland.json"
    tstat = args.work / "stats" / f"{leaf}.timings.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    estat.parent.mkdir(parents=True, exist_ok=True)

    def cleanup() -> float:
        tc0 = time.monotonic()
        for p in (mwm, Path(str(mwm) + ".part"), roads, wetlands, wjson, bjson, ejson, out_tmp):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        return time.monotonic() - tc0

    stages_s: dict[str, float] = {}
    peak_rss_kib: dict[str, int] = {}
    cleaned = False

    t0 = time.monotonic()
    try:
        td0 = time.monotonic()
        prefetch = os.environ.get("NAVI_MWM_PREFETCH_MWM", "").strip()
        if prefetch:
            src = Path(prefetch)
            if not src.is_file():
                raise FileNotFoundError(f"prefetch MWM missing: {src}")
            # Hard-link or copy into the per-leaf tmp path expected by cleanup.
            try:
                if mwm.exists():
                    mwm.unlink()
                os.link(src, mwm)
            except OSError:
                shutil.copy2(src, mwm)
        else:
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
                dest=mwm,
            )
        stages_s["prefetch_or_download"] = round(time.monotonic() - td0, 3)

        dump_cmd = [
            str(args.dump_bin),
            "--mwm",
            str(mwm),
            "--resources_path",
            str(args.resources_path),
            "--out",
            str(roads),
            "--out-wetlands",
            str(wetlands),
        ]
        dump_out, dump_err, dump_rss, dump_s = _run_stage(dump_cmd)
        if dump_out.strip():
            sys.stdout.write(dump_out)
            sys.stdout.flush()
        if dump_err.strip():
            sys.stderr.write(dump_err)
            sys.stderr.flush()
        stages_s["dump"] = round(dump_s, 3)
        peak_rss_kib["dump"] = dump_rss

        python = os.environ.get("NAVI_MWM_PYTHON", sys.executable)
        if args.skip_annotate:
            wjson.write_text("")
            bjson.write_text("")
            wstat.write_text(json.dumps({"skipped": True}) + "\n")
            stages_s["annotate_wait"] = 0.0
            stages_s["annotate"] = 0.0
            peak_rss_kib["annotate"] = 0
        else:
            ann_slots = int(
                os.environ.get("NAVI_MWM_ANNOTATE_CONCURRENCY", str(DEFAULT_ANNOTATE_CONCURRENCY))
                or DEFAULT_ANNOTATE_CONCURRENCY
            )
            ann_lock_dir = Path(
                os.environ.get(
                    "NAVI_MWM_ANNOTATE_LOCK_DIR",
                    str(args.work / "meta" / "annotate-locks"),
                )
            )
            with acquire_annotate_slot(ann_lock_dir, ann_slots) as wait_s:
                ann_out, ann_err, ann_rss, ann_s = _run_stage(
                    [
                        python,
                        str(SCRIPT_DIR / "annotate-wetland-from-mwm.py"),
                        "--roads-jsonl",
                        str(roads),
                        "--wetlands-jsonl",
                        str(wetlands),
                        "--out-wetland",
                        str(wjson),
                        "--out-boardwalk",
                        str(bjson),
                    ]
                )
            if ann_err.strip():
                sys.stderr.write(ann_err)
                sys.stderr.flush()
            wstat.write_text((ann_out or "").strip() + "\n")
            print(ann_out.strip(), flush=True)
            stages_s["annotate_wait"] = round(wait_s, 3)
            stages_s["annotate"] = round(ann_s, 3)
            peak_rss_kib["annotate"] = ann_rss

        if args.skip_elevation:
            ejson.write_text("")
            estat.write_text(json.dumps({"skipped": True}) + "\n")
            stages_s["elev_scan"] = 0.0
            stages_s["elev_fetch"] = 0.0
            stages_s["elev_sample"] = 0.0
            stages_s["elev_total"] = 0.0
            peak_rss_kib["elevation"] = 0
        else:
            elev_cmd = [
                python,
                str(SCRIPT_DIR / "build-elevation-overrides.py"),
                "--roads-jsonl",
                str(roads),
                "--out",
                str(ejson),
                "--cache-dir",
                os.environ["NAVI_MWM_MAPTERHORN_CACHE"],
                "--cache-max-bytes",
                os.environ.get("NAVI_MWM_MAPTERHORN_CACHE_MAX_BYTES", str(48 * 1024**3)),
                "--threshold-m",
                os.environ.get("NAVI_MWM_ELEV_THRESHOLD_M", "2"),
                "--stats-out",
                str(estat),
            ]
            if os.environ.get("NAVI_MWM_CACHE_PARENT_EVICT", "0").strip() == "1":
                elev_cmd.append("--no-evict")
            if args.elev_zoom > 0:
                elev_cmd.extend(["--zoom", str(args.elev_zoom)])
            elev_out, elev_err, elev_rss, elev_s = _run_stage(elev_cmd)
            if elev_out.strip():
                sys.stdout.write(elev_out)
                sys.stdout.flush()
            if elev_err.strip():
                sys.stderr.write(elev_err)
                sys.stderr.flush()
            peak_rss_kib["elevation"] = elev_rss
            stages_s["elev_total"] = round(elev_s, 3)
            try:
                es = json.loads(estat.read_text())
                stages_s["elev_scan"] = float(es.get("elev_scan_s") or 0.0)
                stages_s["elev_fetch"] = float(es.get("elev_fetch_s") or 0.0)
                stages_s["elev_sample"] = float(es.get("elev_sample_s") or 0.0)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                stages_s["elev_scan"] = 0.0
                stages_s["elev_fetch"] = 0.0
                stages_s["elev_sample"] = round(elev_s, 3)

        series = discovery["map_series"]
        ver = str(discovery["version"])
        bake_out, bake_err, bake_rss, bake_s = _run_stage(
            [
                str(args.sidecar_bin),
                "bake",
                "--mwm",
                str(mwm),
                "--wetland-jsonl",
                str(wjson),
                "--boardwalk-jsonl",
                str(bjson),
                "--elevation-jsonl",
                str(ejson),
                "--attribution",
                str(args.attribution),
                "--generator-tag",
                f"comaps-release/{series}",
                "--generator-sha",
                ver,
                "--ambiguous-threshold",
                os.environ.get("NAVI_MWM_AMBIGUOUS_THRESHOLD", "0"),
                "--out",
                str(out_tmp),
            ]
        )
        if bake_err.strip():
            sys.stderr.write(bake_err)
            sys.stderr.flush()
        os.replace(out_tmp, out)
        (args.work / "stats" / f"{leaf}.sidecar.json").write_text(bake_out)
        print(bake_out.strip(), flush=True)
        stages_s["bake"] = round(bake_s, 3)
        peak_rss_kib["bake"] = bake_rss

        wall_before_hash = time.monotonic() - t0
        hash_s = record_processed(
            state_path,
            leaf,
            mwm,
            out,
            discovery,
            wall_s=wall_before_hash,
        )
        stages_s["hash"] = round(hash_s, 3)

        cleanup_s = cleanup()
        cleaned = True
        stages_s["cleanup"] = round(cleanup_s, 3)

        wall = time.monotonic() - t0
        peak_rss_kib["leaf_self"] = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)

        timings_doc = {
            "leaf": leaf,
            "wall_s": round(wall, 3),
            "stages_s": stages_s,
            "peak_rss_kib": peak_rss_kib,
        }
        tstat.write_text(json.dumps(timings_doc, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "leaf": leaf,
                    "ok": True,
                    "wall_s": round(wall, 1),
                    "stages_s": stages_s,
                    "peak_rss_kib": peak_rss_kib,
                }
            ),
            flush=True,
        )
        return 0
    except Exception as e:
        print(json.dumps({"leaf": leaf, "ok": False, "error": str(e)}), flush=True)
        raise
    finally:
        if not cleaned:
            cleanup()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as e:
        sys.stderr.write(f"subprocess failed: {e}\n")
        if e.stdout:
            sys.stderr.write(e.stdout)
        if e.stderr:
            sys.stderr.write(e.stderr)
        raise SystemExit(e.returncode or 1)
