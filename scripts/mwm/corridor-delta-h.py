#!/usr/bin/env python3
"""Road-follow climb/descent for Østlandet corridors using dumped MWM road altitudes
vs Mapterhorn samples at a given zoom (threshold compare elsewhere).

For each corridor, densify the straight segment, snap each sample to the nearest
dumped road point (within max_snap_m), and accumulate climb/descent on the snapped
CoMaps altitudes and on Mapterhorn samples.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "elev", SCRIPT_DIR / "build-elevation-overrides.py"
)
elev = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(elev)

CORRIDORS = [
    ("Valdresflye", 61.387, 8.81, 61.45, 8.9),
    ("Sognefjellet", 61.55, 7.95, 61.65, 8.15),
    ("Rv27_Hardangervidda_E", 60.35, 7.55, 60.45, 7.75),
    ("Telemark_Gaustatoppen_approach", 59.85, 8.65, 59.95, 8.75),
    ("Buskerud_Hemsedal", 60.85, 8.45, 60.95, 8.6),
    ("Oppland_Rondane_Rv27", 61.85, 10.0, 61.95, 10.2),
    ("Oppland_Jotunheimen_E", 61.55, 8.4, 61.65, 8.55),
    ("Buskerud_Hardangervidda_N", 60.55, 7.55, 60.65, 7.7),
]


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def densify(lat1, lon1, lat2, lon2, n: int):
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.0
        yield lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t


def load_road_points(paths: list[Path]):
    from shapely.geometry import Point
    from shapely.strtree import STRtree

    pts = []
    alts = []
    for path in paths:
        with path.open() as f:
            for line in f:
                if not line.strip():
                    continue
                feat = json.loads(line)
                for p in feat.get("points") or []:
                    pts.append(Point(float(p["lon"]), float(p["lat"])))
                    alts.append(float(p.get("alt_m", 0)))
    tree = STRtree(pts) if pts else None
    return tree, pts, alts


def climb_descent(series: list[float]) -> tuple[float, float]:
    up = down = 0.0
    for a, b in zip(series, series[1:]):
        d = b - a
        if d > 0:
            up += d
        else:
            down += -d
    return up, down


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roads-jsonl", type=Path, nargs="+", required=True)
    ap.add_argument("--cache-dir", type=Path, required=True)
    ap.add_argument("--zoom", type=int, default=12)
    ap.add_argument("--samples", type=int, default=80)
    ap.add_argument("--max-snap-m", type=float, default=250.0)
    ap.add_argument("--out-json", type=Path, required=True)
    args = ap.parse_args()

    t0 = time.monotonic()
    tree, pts, alts = load_road_points(args.roads_jsonl)
    cache = elev.TileCache(args.cache_dir, 8 * 1024**3, args.zoom)
    corridors = []
    for name, lat1, lon1, lat2, lon2 in CORRIDORS:
        comaps_series = []
        mh_series = []
        snapped = 0
        max_dev = 0.0
        for lat, lon in densify(lat1, lon1, lat2, lon2, args.samples):
            mh = cache.sample(lat, lon)
            if tree is None:
                continue
            from shapely.geometry import Point

            idx = tree.nearest(Point(lon, lat))
            if hasattr(idx, "item"):
                idx = int(idx.item())
            else:
                idx = int(idx)
            near = pts[idx]
            dist = haversine_m(lat, lon, near.y, near.x)
            if dist > args.max_snap_m:
                continue
            snapped += 1
            ca = alts[idx]
            comaps_series.append(ca)
            if mh is not None:
                mh_series.append(mh)
                max_dev = max(max_dev, abs(mh - ca))
        c_up, c_dn = climb_descent(comaps_series) if len(comaps_series) > 1 else (0.0, 0.0)
        m_up, m_dn = climb_descent(mh_series) if len(mh_series) > 1 else (0.0, 0.0)
        corridors.append(
            {
                "name": name,
                "snapped_samples": snapped,
                "comaps_climb_m": round(c_up, 1),
                "comaps_descent_m": round(c_dn, 1),
                f"mapterhorn_z{args.zoom}_climb_m": round(m_up, 1),
                f"mapterhorn_z{args.zoom}_descent_m": round(m_dn, 1),
                "max_abs_deviation_m": round(max_dev, 2),
                "climb_delta_m": round(m_up - c_up, 1),
                "descent_delta_m": round(m_dn - c_dn, 1),
            }
        )

    report = {
        "zoom": args.zoom,
        "samples_per_corridor": args.samples,
        "max_snap_m": args.max_snap_m,
        "roads": [str(p) for p in args.roads_jsonl],
        "wall_s": round(time.monotonic() - t0, 1),
        "corridors": corridors,
        "note": "Snapped straight-line corridor samples to nearest MWM road point; not a routed path.",
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
