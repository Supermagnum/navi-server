#!/usr/bin/env python3
"""Compare elevation overrides at max Mapterhorn zoom vs z12 for Østlandet leaves.

Also road-follow climb/descent on corridors vs CoMaps altitudes and optional
Navi live pack delta_h JSON.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def run_elev(roads: Path, out: Path, cache: Path, zoom: int, threshold: float, stats: Path) -> dict:
    env = os.environ.copy()
    cmd = [
        env.get("NAVI_MWM_PYTHON", sys.executable),
        str(SCRIPT_DIR / "build-elevation-overrides.py"),
        "--roads-jsonl",
        str(roads),
        "--out",
        str(out),
        "--cache-dir",
        str(cache),
        "--cache-max-bytes",
        env.get("NAVI_MWM_MAPTERHORN_CACHE_MAX_BYTES", str(8 * 1024**3)),
        "--threshold-m",
        str(threshold),
        "--zoom",
        str(zoom),
        "--stats-out",
        str(stats),
    ]
    t0 = time.monotonic()
    subprocess.check_call(cmd, env=env)
    wall = time.monotonic() - t0
    data = json.loads(stats.read_text())
    data["wall_s_measured"] = wall
    data["sidecar_jsonl_bytes"] = out.stat().st_size if out.exists() else 0
    return data


def sample_corridor_alts(roads: Path, elev_jsonl: Path, corridor: dict) -> dict:
    """Approximate climb along corridor using nearest dumped road points."""
    # Load sparse overrides
    overrides: dict[tuple[int, int], float] = {}
    if elev_jsonl.is_file():
        for line in elev_jsonl.open():
            if not line.strip():
                continue
            feat = json.loads(line)
            fid = int(feat["feature_id"])
            for p in feat.get("points") or []:
                overrides[(fid, int(p["point_index"]))] = float(p["altitude_dm"]) / 10.0

    # Walk roads near corridor endpoints — coarse: use all points in bbox
    west = min(corridor["from"][1], corridor["to"][1]) - 0.15
    east = max(corridor["from"][1], corridor["to"][1]) + 0.15
    south = min(corridor["from"][0], corridor["to"][0]) - 0.15
    north = max(corridor["from"][0], corridor["to"][0]) + 0.15

    comaps_alts = []
    mh_alts = []
    for line in roads.open():
        if not line.strip():
            continue
        feat = json.loads(line)
        fid = int(feat["feature_id"])
        pts = feat.get("points") or []
        for i, p in enumerate(pts):
            lat, lon = float(p["lat"]), float(p["lon"])
            if not (south <= lat <= north and west <= lon <= east):
                continue
            c = float(p.get("alt_m", 0))
            comaps_alts.append(c)
            mh = overrides.get((fid, i), c)
            mh_alts.append(mh)

    def climb_descent(alts: list[float]) -> tuple[float, float]:
        up = down = 0.0
        for a, b in zip(alts, alts[1:]):
            d = b - a
            if d > 0:
                up += d
            else:
                down += -d
        return up, down

    c_up, c_dn = climb_descent(comaps_alts) if len(comaps_alts) > 1 else (0.0, 0.0)
    m_up, m_dn = climb_descent(mh_alts) if len(mh_alts) > 1 else (0.0, 0.0)
    max_dev = 0.0
    for a, b in zip(comaps_alts, mh_alts):
        max_dev = max(max_dev, abs(a - b))
    return {
        "points": len(comaps_alts),
        "comaps_climb_m": round(c_up, 1),
        "comaps_descent_m": round(c_dn, 1),
        "override_climb_m": round(m_up, 1),
        "override_descent_m": round(m_dn, 1),
        "max_deviation_m": round(max_dev, 2),
    }


# Spike2 Østlandet mountain corridors (lat,lon)
DEFAULT_CORRIDORS = [
    {"id": "hamar_elverum", "from": [60.7945, 11.0680], "to": [60.8820, 11.5600]},
    {"id": "elverum_lillehammer", "from": [60.8820, 11.5600], "to": [61.1150, 10.4660]},
    {"id": "lillehammer_gausdal", "from": [61.1150, 10.4660], "to": [61.2300, 10.2200]},
    {"id": "ottra_lom", "from": [61.7000, 8.5700], "to": [61.8370, 8.5690]},
    {"id": "geilo_holst", "from": [60.5340, 8.2070], "to": [60.6200, 8.3000]},
    {"id": "rjukan_gaustatoppen", "from": [59.8800, 8.5940], "to": [59.8700, 8.6500]},
    {"id": "norddal_trollstigen", "from": [62.4500, 7.5000], "to": [62.4560, 7.6800]},
    {"id": "dovre_hjerkinn", "from": [62.0200, 9.1800], "to": [62.2200, 9.5500]},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roads-dir", type=Path, required=True)
    ap.add_argument("--leaves", default="Norway_Hedmark,Norway_Oppland,Norway_Central,Norway_Southern")
    ap.add_argument("--cache-dir", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--threshold-m", type=float, default=2.0)
    ap.add_argument("--max-zoom", type=int, default=15)
    ap.add_argument("--navi-delta-h", type=Path, default=None)
    args = ap.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    report = {"leaves": {}, "corridors": [], "recommendation": {}}
    for leaf in [x.strip() for x in args.leaves.split(",") if x.strip()]:
        roads = args.roads_dir / f"{leaf}.roads.jsonl"
        if not roads.is_file():
            report["leaves"][leaf] = {"error": "missing roads jsonl"}
            continue
        z12_out = args.out_dir / f"{leaf}.elev.z12.jsonl"
        zmax_out = args.out_dir / f"{leaf}.elev.zmax.jsonl"
        z12_stats = args.out_dir / f"{leaf}.elev.z12.stats.json"
        zmax_stats = args.out_dir / f"{leaf}.elev.zmax.stats.json"
        s12 = run_elev(roads, z12_out, args.cache_dir, 12, args.threshold_m, z12_stats)
        smax = run_elev(roads, zmax_out, args.cache_dir, args.max_zoom, args.threshold_m, zmax_stats)
        cache_bytes = sum(p.stat().st_size for p in args.cache_dir.rglob("*.webp"))
        report["leaves"][leaf] = {
            "z12": s12,
            "zmax": smax,
            "cache_bytes_after": cache_bytes,
            "override_rate_ratio_zmax_vs_z12": (
                (smax.get("override_rate") or 0) / (s12.get("override_rate") or 1e-12)
            ),
            "size_ratio_zmax_vs_z12": (
                (smax.get("sidecar_jsonl_bytes") or 0)
                / max(1, s12.get("sidecar_jsonl_bytes") or 1)
            ),
        }

    # Corridors use Hedmark/Oppland roads preferentially
    roads_h = args.roads_dir / "Norway_Hedmark.roads.jsonl"
    elev_z12 = args.out_dir / "Norway_Hedmark.elev.z12.jsonl"
    elev_zmax = args.out_dir / "Norway_Hedmark.elev.zmax.jsonl"
    navi = {}
    if args.navi_delta_h and args.navi_delta_h.is_file():
        navi = json.loads(args.navi_delta_h.read_text())

    for c in DEFAULT_CORRIDORS:
        entry = {"id": c["id"], "from": c["from"], "to": c["to"]}
        if roads_h.is_file():
            entry["comaps_vs_z12"] = sample_corridor_alts(roads_h, elev_z12, c)
            entry["comaps_vs_zmax"] = sample_corridor_alts(roads_h, elev_zmax, c)
        if navi:
            entry["navi_live"] = navi.get(c["id"]) or navi.get(c["id"].replace("_", "-"))
        report["corridors"].append(entry)

    # Recommendation
    rates_z12 = [v["z12"].get("override_rate", 0) for v in report["leaves"].values() if "z12" in v]
    rates_max = [v["zmax"].get("override_rate", 0) for v in report["leaves"].values() if "zmax" in v]
    report["recommendation"] = {
        "production_zoom": 12,
        "rationale": (
            "z12 keeps override rates ~few percent with warmable cache; "
            "max zoom increases override density and cache footprint with "
            "diminishing corridor delta_h benefit at 2m threshold."
            if rates_max and rates_z12 and (sum(rates_max) / len(rates_max)) > 1.3 * (sum(rates_z12) / len(rates_z12))
            else "Prefer highest available zoom with fallback when cache budget allows; "
            "otherwise z12 is the scratch-proven default."
        ),
        "cache_max_bytes_world": 32 * 1024**3,
        "cache_eviction": "mtime LRU of *.webp under cache dir (existing TileCache)",
        "mean_override_rate_z12": sum(rates_z12) / len(rates_z12) if rates_z12 else None,
        "mean_override_rate_zmax": sum(rates_max) / len(rates_max) if rates_max else None,
    }
    out = args.out_dir / "elevation_zoom_compare.json"
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"out": str(out), "recommendation": report["recommendation"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
