#!/usr/bin/env python3
"""Summarize a comaps-release world bake: continent walls, cache, failures."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))
from leaf_scheduler import is_europe, is_nordic  # noqa: E402


def continent(leaf: str) -> str:
    if is_nordic(leaf):
        return "nordic"
    if is_europe(leaf):
        return "europe"
    return "world"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    proc = json.loads((args.work / "meta" / "processed.json").read_text())
    leaves = proc.get("leaves") or {}
    run = {}
    rs = args.work / "meta" / "run-summary.json"
    if rs.is_file():
        run = json.loads(rs.read_text())

    by = {"nordic": [], "europe": [], "world": []}
    hits = misses = 0
    for leaf, meta in leaves.items():
        bucket = continent(leaf)
        wall = meta.get("wall_s")
        by[bucket].append({"leaf": leaf, "wall_s": wall})
        sp = args.work / "stats" / f"{leaf}.elevation.json"
        if sp.is_file():
            s = json.loads(sp.read_text())
            hits += int(s.get("tile_cache_hits") or 0)
            misses += int(s.get("tile_cache_misses") or 0)

    sidecar_bytes = sum(
        p.stat().st_size for p in (args.work / "sidecars").glob("*.navi-sidecar.v1.zst")
    ) if (args.work / "sidecars").is_dir() else 0

    lookups = hits + misses
    out = {
        "processed_leaves": len(leaves),
        "run_summary": run,
        "continents": {
            k: {
                "n": len(v),
                "wall_s_sum": round(sum((x.get("wall_s") or 0) for x in v), 1),
            }
            for k, v in by.items()
        },
        "tile_cache_hits": hits,
        "tile_cache_misses": misses,
        "tile_cache_hit_rate": (hits / lookups) if lookups else None,
        "sidecar_total_bytes": sidecar_bytes,
        "failed": run.get("failed") or [],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
