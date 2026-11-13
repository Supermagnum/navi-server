#!/usr/bin/env python3
"""Process one CoMaps leaf into a Navi sidecar (download → dump → annotate → elev → bake).

Designed for parallel invocation. Updates processed.json atomically under a file lock.
Deletes the MWM and intermediate JSONL after success (streaming).
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))
from comaps_release import download_leaf, LeafInfo  # noqa: E402
from leaf_scheduler import load_processed, save_processed_atomic  # noqa: E402


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
) -> None:
    meta = discovery["leaves"][leaf]
    mwm_sha, mwm_n = sha256_file(mwm)
    sc_sha, sc_n = sha256_file(out)
    with state_path.open("a+") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
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

    # Skip if already done (race-safe check)
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
    estat = args.work / "stats" / f"{leaf}.elevation.json"
    wstat = args.work / "stats" / f"{leaf}.wetland.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    estat.parent.mkdir(parents=True, exist_ok=True)

    def cleanup() -> None:
        for p in (mwm, Path(str(mwm) + ".part"), roads, wetlands, wjson, bjson, ejson):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    t0 = time.monotonic()
    try:
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
                import shutil

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
        subprocess.check_call(dump_cmd)

        python = os.environ.get("NAVI_MWM_PYTHON", sys.executable)
        if args.skip_annotate:
            wjson.write_text("")
            bjson.write_text("")
            wstat.write_text(json.dumps({"skipped": True}) + "\n")
        else:
            r = subprocess.run(
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
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            wstat.write_text((r.stdout or "").strip() + "\n")
            print(r.stdout.strip(), flush=True)

        if args.skip_elevation:
            ejson.write_text("")
            estat.write_text(json.dumps({"skipped": True}) + "\n")
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
            if args.elev_zoom > 0:
                elev_cmd.extend(["--zoom", str(args.elev_zoom)])
            subprocess.check_call(elev_cmd)

        series = discovery["map_series"]
        ver = str(discovery["version"])
        bake = subprocess.run(
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
                str(out),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        (args.work / "stats" / f"{leaf}.sidecar.json").write_text(bake.stdout)
        print(bake.stdout.strip(), flush=True)

        wall = time.monotonic() - t0
        record_processed(state_path, leaf, mwm, out, discovery, wall_s=wall)
        print(json.dumps({"leaf": leaf, "ok": True, "wall_s": round(wall, 1)}), flush=True)
        return 0
    except Exception as e:
        print(json.dumps({"leaf": leaf, "ok": False, "error": str(e)}), flush=True)
        raise
    finally:
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
