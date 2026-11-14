#!/usr/bin/env python3
"""Next-release gate: ~20 leaves cold vs geometry-cache must bake identical data sections.

On by default when NAVI_MWM_CACHE_IDENTITY_GATE is unset/1. Set to 0/false/off to skip.
Compares wetland_edges, boardwalk_flags, elevation_overrides (not attribution).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import zstandard as zstd

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_LEAVES = [
    "Norway_Hedmark",
    "Sweden_Stockholm",
    "Germany_Berlin",
    "Albania",
    "Russia_Moscow",
    "Bolivia_South",
    "Ecuador_East",
    "Ecuador_West",
    "Colombia_West",
    "Kenya",
    "Uganda",
    "Indonesia_West",
    "Indonesia_Jawa Tengah",
    "Fiji",
    "China_Tibet Autonomous Region",
    "India_Jammu and Kashmir",
    "Ethiopia",
    "Austria_Tyrol",
    "US_Colorado_Aspen",
    "Faroe Islands",
    "Iceland",
]


def gate_enabled() -> bool:
    raw = os.environ.get("NAVI_MWM_CACHE_IDENTITY_GATE", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def section_data_digests(path: Path) -> dict[str, str]:
    data = zstd.ZstdDecompressor().decompress(path.read_bytes())
    off = 8
    for _ in range(2):
        n = struct.unpack_from("<H", data, off)[0]
        off += 2 + n
    off += 32 + 8
    scount = struct.unpack_from("<I", data, off)[0]
    off += 4
    sections = []
    for _ in range(scount):
        n = struct.unpack_from("<H", data, off)[0]
        off += 2
        name = data[off : off + n].decode()
        off += n
        uncomp, comp = struct.unpack_from("<QQ", data, off)
        off += 16 + 32
        soff = struct.unpack_from("<Q", data, off)[0]
        off += 8
        sections.append((name, uncomp, comp, soff))
    payload = data[off:]
    out = {}
    for name, uncomp, comp, soff in sections:
        if name == "attribution":
            continue
        body = zstd.ZstdDecompressor().decompress(payload[soff : soff + comp])
        out[name] = hashlib.sha256(body).hexdigest()
    return out


def _run_leaf(
    *,
    leaf: str,
    work: Path,
    discovery: Path,
    dump_bin: Path,
    sidecar_bin: Path,
    resources: Path,
    attribution: Path,
    gcache: Path,
    write_cache: bool,
) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["NAVI_MWM_GEOMETRY_CACHE"] = str(gcache)
    env["NAVI_MWM_GEOMETRY_CACHE_ENABLE"] = "1"
    env["NAVI_MWM_GEOMETRY_CACHE_WRITE"] = "1" if write_cache else "0"
    env["NAVI_MWM_LEAF"] = leaf
    cmd = [
        env.get("NAVI_MWM_PYTHON", sys.executable),
        str(SCRIPT_DIR / "process_comaps_leaf.py"),
        "--leaf",
        leaf,
        "--work",
        str(work),
        "--discovery",
        str(discovery),
        "--dump-bin",
        str(dump_bin),
        "--sidecar-bin",
        str(sidecar_bin),
        "--resources-path",
        str(resources),
        "--attribution",
        str(attribution),
    ]
    subprocess.check_call(cmd, env=env)
    out = work / "sidecars" / f"{leaf}.navi-sidecar.v1.zst"
    if not out.is_file():
        raise SystemExit(f"gate: missing sidecar for {leaf}: {out}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discovery", type=Path, required=True)
    ap.add_argument("--dump-bin", type=Path, required=True)
    ap.add_argument("--sidecar-bin", type=Path, required=True)
    ap.add_argument("--resources-path", type=Path, required=True)
    ap.add_argument("--attribution", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--leaves-file", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    if not gate_enabled():
        print(json.dumps({"cache_identity_gate": "skipped"}))
        return 0

    disc = json.loads(args.discovery.read_text())
    if args.leaves_file and args.leaves_file.is_file():
        wanted = [
            ln.strip()
            for ln in args.leaves_file.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")
        ]
    else:
        wanted = list(DEFAULT_LEAVES)
    leaves = [L for L in wanted if L in disc["leaves"]][: max(1, args.limit)]
    if len(leaves) < min(5, args.limit):
        raise SystemExit(f"cache identity gate: too few leaves available: {leaves}")

    gate_root = args.work / "meta" / "cache-identity-gate"
    if gate_root.exists():
        shutil.rmtree(gate_root)
    gate_root.mkdir(parents=True)
    gcache = gate_root / "gcache"
    cold_work = gate_root / "cold"
    warm_work = gate_root / "warm"

    results = []
    for leaf in leaves:
        cold_sc = _run_leaf(
            leaf=leaf,
            work=cold_work,
            discovery=args.discovery,
            dump_bin=args.dump_bin,
            sidecar_bin=args.sidecar_bin,
            resources=args.resources_path,
            attribution=args.attribution,
            gcache=gcache,
            write_cache=True,
        )
        # Reset warm processed so the leaf runs again with cache hits.
        warm_state = warm_work / "meta" / "processed.json"
        if warm_state.is_file():
            st = json.loads(warm_state.read_text())
            st.get("leaves", {}).pop(leaf, None)
            warm_state.write_text(json.dumps(st) + "\n")
        warm_sc = _run_leaf(
            leaf=leaf,
            work=warm_work,
            discovery=args.discovery,
            dump_bin=args.dump_bin,
            sidecar_bin=args.sidecar_bin,
            resources=args.resources_path,
            attribution=args.attribution,
            gcache=gcache,
            write_cache=False,
        )
        dc = section_data_digests(cold_sc)
        dw = section_data_digests(warm_sc)
        ok = dc == dw
        results.append({"leaf": leaf, "ok": ok, "cold": dc, "warm": dw})
        print(json.dumps({"cache_identity_leaf": leaf, "ok": ok}), flush=True)
        if not ok:
            (gate_root / "failure.json").write_text(
                json.dumps(results, indent=2) + "\n"
            )
            raise SystemExit(
                f"cache identity gate FAILED on {leaf}: data sections differ"
            )

    (gate_root / "ok.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({"cache_identity_gate": "ok", "leaves": len(results)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
