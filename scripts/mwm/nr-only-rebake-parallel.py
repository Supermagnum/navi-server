#!/usr/bin/env python3
"""NR-only world pass: annotate-named-routes + repack-named-routes per leaf.

Reuses existing sidecars (wetland / boardwalk / elevation / vehicle_limits /
MWM fingerprint). Does NOT download MWMs or re-run wetland/elev/VL.

Usage (as navit-server or with write ACL):
  python3 /tmp/navi-nr-261115-nr-only-parallel.py \\
    --work /media/navi/navi-server/data/mwm/maps_build/comaps-release/261115 \\
    --pbf /media/navi/haaken-scratch/osm-named-routes/planet-named-routes.osm.pbf \\
    --concurrency 4
"""
from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MIN_PBF_BYTES = 1_000_000


def sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def update_processed_sidecar(state_path: Path, leaf: str, sidecar: Path) -> None:
    """Update sidecar_sha256 only — never delete processed leaf entries."""
    sc_sha, sc_n = sha256_file(sidecar)
    lock_path = state_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            st = json.loads(state_path.read_text()) if state_path.is_file() else {"leaves": {}}
            leaves = st.setdefault("leaves", {})
            rec = leaves.get(leaf) or {}
            rec["sidecar_bytes"] = sc_n
            rec["sidecar_sha256"] = sc_sha
            rec["named_routes_repacked_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            leaves[leaf] = rec
            tmp = state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(st, indent=2) + "\n")
            os.replace(tmp, state_path)
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def process_leaf(
    *,
    leaf: str,
    work: Path,
    pbf: Path,
    borders: Path,
    annotate_py: Path,
    sidecar_bin: Path,
    place_source_bin: Path,
    python: str,
) -> dict:
    t0 = time.monotonic()
    sc = work / "sidecars" / f"{leaf}.navi-sidecar.v1.zst"
    if not sc.is_file():
        return {"leaf": leaf, "ok": False, "error": f"missing sidecar {sc}"}
    poly = borders / f"{leaf}.poly"
    meta_nr = work / "meta" / "named_routes"
    meta_nr.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=f"nr-{leaf}.") as td:
        td_path = Path(td)
        nrjson = td_path / f"{leaf}.named_routes.jsonl"
        nrreport = td_path / f"{leaf}.named_routes.report.json"
        env = os.environ.copy()
        env["NAVI_PLACE_SOURCE_BIN"] = str(place_source_bin)
        cmd = [
            python,
            str(annotate_py),
            "--pbf",
            str(pbf),
            "--out-jsonl",
            str(nrjson),
            "--report-json",
            str(nrreport),
        ]
        if poly.is_file():
            cmd.extend(["--poly", str(poly)])
        try:
            subprocess.check_call(cmd, env=env, stdout=subprocess.DEVNULL)
        except subprocess.CalledProcessError as e:
            return {
                "leaf": leaf,
                "ok": False,
                "error": f"annotate failed rc={e.returncode}",
                "wall_s": round(time.monotonic() - t0, 2),
            }

        routes = 0
        if nrreport.is_file():
            try:
                routes = int(json.loads(nrreport.read_text()).get("routes") or 0)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                routes = 0
            shutil.copy2(nrreport, meta_nr / f"{leaf}.report.json")

        repack_cmd = [
            str(sidecar_bin),
            "repack-named-routes",
            "--sidecar",
            str(sc),
            "--named-routes-jsonl",
            str(nrjson),
            "--out",
            str(sc),
        ]
        try:
            out = subprocess.check_output(repack_cmd, text=True, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            return {
                "leaf": leaf,
                "ok": False,
                "error": f"repack failed: {(e.output or '')[:500]}",
                "wall_s": round(time.monotonic() - t0, 2),
            }

        update_processed_sidecar(work / "meta" / "processed.json", leaf, sc)
        wall = round(time.monotonic() - t0, 2)
        rec = {"leaf": leaf, "ok": True, "routes": routes, "wall_s": wall}
        try:
            baked = json.loads(out.strip().splitlines()[-1])
            rec["named_routes_records"] = baked.get("named_routes_records")
            rec["wetland_records"] = baked.get("wetland_records")
            rec["mwm_sha256"] = baked.get("mwm_sha256")
        except (json.JSONDecodeError, IndexError, TypeError):
            pass
        return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--pbf", type=Path, required=True)
    ap.add_argument("--borders", type=Path, required=True)
    ap.add_argument("--annotate-py", type=Path, required=True)
    ap.add_argument("--sidecar-bin", type=Path, required=True)
    ap.add_argument("--place-source-bin", type=Path, required=True)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--leaf", type=str, default="", help="Single leaf (smoke)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    if not args.pbf.is_file() or args.pbf.stat().st_size < MIN_PBF_BYTES:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"NR PBF missing/too small (<{MIN_PBF_BYTES}): {args.pbf}",
                    "pbf_bytes": args.pbf.stat().st_size if args.pbf.is_file() else 0,
                }
            ),
            flush=True,
        )
        return 2
    if not args.sidecar_bin.is_file():
        print(json.dumps({"ok": False, "error": f"missing {args.sidecar_bin}"}), flush=True)
        return 2
    try:
        help_txt = subprocess.check_output(
            [str(args.sidecar_bin), "repack-named-routes", "--help"], text=True
        )
    except subprocess.CalledProcessError as e:
        print(
            json.dumps({"ok": False, "error": f"repack-named-routes help failed: {e}"}),
            flush=True,
        )
        return 2
    if "named-routes-jsonl" not in help_txt:
        print(json.dumps({"ok": False, "error": "sidecar lacks repack-named-routes"}), flush=True)
        return 2

    sidecars = args.work / "sidecars"
    if args.leaf:
        leaves = [args.leaf]
    else:
        leaves = sorted(
            p.name.replace(".navi-sidecar.v1.zst", "")
            for p in sidecars.glob("*.navi-sidecar.v1.zst")
        )
    if args.limit > 0:
        leaves = leaves[: args.limit]

    conc = max(1, int(args.concurrency))
    print(
        json.dumps(
            {
                "nr_only_start": True,
                "leaves": len(leaves),
                "concurrency": conc,
                "pbf": str(args.pbf),
                "pbf_bytes": args.pbf.stat().st_size,
            }
        ),
        flush=True,
    )

    ok_n = 0
    fail_n = 0
    routes_sum = 0
    t0 = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=conc) as pool:
        futs = [
            pool.submit(
                process_leaf,
                leaf=leaf,
                work=args.work,
                pbf=args.pbf,
                borders=args.borders,
                annotate_py=args.annotate_py,
                sidecar_bin=args.sidecar_bin,
                place_source_bin=args.place_source_bin,
                python=args.python,
            )
            for leaf in leaves
        ]
        for i, fut in enumerate(concurrent.futures.as_completed(futs), 1):
            rec = fut.result()
            if rec.get("ok"):
                ok_n += 1
                routes_sum += int(rec.get("routes") or 0)
            else:
                fail_n += 1
            print(json.dumps(rec), flush=True)
            if i % 25 == 0 or i == len(leaves):
                elapsed = time.monotonic() - t0
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(leaves) - i) / rate if rate > 0 else 0
                print(
                    json.dumps(
                        {
                            "progress": i,
                            "total": len(leaves),
                            "ok": ok_n,
                            "fail": fail_n,
                            "routes_sum": routes_sum,
                            "elapsed_s": round(elapsed, 1),
                            "eta_s": round(eta, 1),
                        }
                    ),
                    flush=True,
                )

    print(
        json.dumps(
            {
                "nr_only_done": True,
                "ok": ok_n,
                "fail": fail_n,
                "routes_sum": routes_sum,
                "wall_s": round(time.monotonic() - t0, 1),
            }
        ),
        flush=True,
    )
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
