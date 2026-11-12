#!/usr/bin/env python3
"""Road-follow climb/descent for Østlandet corridors.

Compares CoMaps altitudes (from dump_mwm_roads) with Mapterhorn samples at the
**same** road geometry points. Also reports known spot heights.

IMPORTANT: never compare Mapterhorn sampled at an off-road densify point with
CoMaps altitude of a nearby snapped road — that produced bogus ~300 m
deviations (spatial mismatch, not a units/decode bug).
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

# Corridor = bbox around a mountain road corridor (min_lat, min_lon, max_lat, max_lon)
CORRIDORS = [
    ("Valdresflye", 61.37, 8.78, 61.46, 8.92),
    ("Sognefjellet", 61.53, 7.90, 61.67, 8.20),
    ("Rv27_Hardangervidda_E", 60.33, 7.50, 60.47, 7.80),
    ("Telemark_Gaustatoppen_approach", 59.84, 8.62, 59.96, 8.78),
    ("Buskerud_Hemsedal", 60.83, 8.40, 60.97, 8.65),
    ("Oppland_Rondane_Rv27", 61.83, 9.95, 61.97, 10.25),
    ("Oppland_Jotunheimen_E", 61.53, 8.35, 61.67, 8.60),
    ("Buskerud_Hardangervidda_N", 60.53, 7.50, 60.67, 7.75),
]

# Ground-truth spot heights (name, lat, lon, expected_m, source note)
SPOTS = [
    ("Gaustatoppen_road_high", 59.8719, 8.72992, 943, "dump nearest to summit road"),
    ("Mjosa_Hamar_waterfront", 60.7945, 11.0680, 123, "Lake Mjøsa surface ~123 m"),
    ("Valdresflye_pass", 61.387, 8.81, 1389, "pass ~1380–1390 m"),
    ("Oslo_harbour", 59.908, 10.738, 1, "sea level approx"),
    ("Gaustatoppen_summit", 59.8706, 8.6528, 1883, "summit peak (no road)"),
    ("Hemsedalsfjellet", 60.88, 8.52, 1100, "plateau approx"),
    ("Rondane_Rv27", 61.90, 10.10, 1000, "highland road approx"),
    ("Hardangervidda_E", 60.40, 7.65, 1200, "plateau approx"),
    ("Jotunheimen_E", 61.60, 8.48, 1400, "highland approx"),
    ("Lillehammer_town", 61.115, 10.466, 200, "town ~200 m"),
]


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def climb_descent(series: list[float]) -> tuple[float, float]:
    up = down = 0.0
    for a, b in zip(series, series[1:]):
        d = b - a
        if d > 0:
            up += d
        else:
            down += -d
    return up, down


def load_features(paths: list[Path]) -> list[dict]:
    feats = []
    for path in paths:
        with path.open() as f:
            for line in f:
                if not line.strip():
                    continue
                feats.append(json.loads(line))
    return feats


def feature_in_bbox(feat: dict, min_lat, min_lon, max_lat, max_lon) -> bool:
    for p in feat.get("points") or []:
        lat, lon = float(p["lat"]), float(p["lon"])
        if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--roads-jsonl", type=Path, nargs="+", required=True)
    ap.add_argument("--cache-dir", type=Path, required=True)
    ap.add_argument("--zoom", type=int, default=12)
    ap.add_argument("--threshold-m", type=float, default=2.0)
    ap.add_argument("--max-points-per-corridor", type=int, default=5000)
    ap.add_argument("--out-json", type=Path, required=True)
    args = ap.parse_args()

    t0 = time.monotonic()
    feats = load_features(args.roads_jsonl)
    cache = elev.TileCache(args.cache_dir, 8 * 1024**3, args.zoom)

    # Spot heights: sample Mapterhorn at exact lat/lon; if a road point is within
    # 50 m, also report CoMaps altitude at that point.
    spot_rows = []
    for name, lat, lon, expected, note in SPOTS:
        mh = cache.sample(lat, lon)
        nearest = None
        nearest_d = 1e18
        for feat in feats:
            for p in feat.get("points") or []:
                d = haversine_m(lat, lon, float(p["lat"]), float(p["lon"]))
                if d < nearest_d:
                    nearest_d = d
                    nearest = p
        comaps = None
        mh_at_road = None
        if nearest is not None and nearest_d <= 50.0:
            comaps = float(nearest.get("alt_m", 0))
            mh_at_road = cache.sample(float(nearest["lat"]), float(nearest["lon"]))
        spot_rows.append(
            {
                "name": name,
                "lat": lat,
                "lon": lon,
                "expected_m": expected,
                "mapterhorn_m": None if mh is None else round(mh, 1),
                "mapterhorn_minus_expected_m": None
                if mh is None
                else round(mh - expected, 1),
                "nearest_road_m": None if nearest is None else round(nearest_d, 1),
                "comaps_alt_m": comaps,
                "mapterhorn_at_road_m": None if mh_at_road is None else round(mh_at_road, 1),
                "same_point_dev_m": None
                if mh_at_road is None or comaps is None
                else round(abs(mh_at_road - comaps), 2),
                "note": note,
            }
        )

    corridors = []
    for name, min_lat, min_lon, max_lat, max_lon in CORRIDORS:
        same_point_devs: list[float] = []
        n_pts = 0
        n_override = 0
        c_up = c_dn = m_up = m_dn = 0.0
        for feat in feats:
            if not feature_in_bbox(feat, min_lat, min_lon, max_lat, max_lon):
                continue
            # Per-feature climb/descent along road order (not bbox-unsorted pool).
            feat_comaps: list[float] = []
            feat_mh: list[float] = []
            for p in feat.get("points") or []:
                lat, lon = float(p["lat"]), float(p["lon"])
                if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                    continue
                ca = float(p.get("alt_m", 0))
                mh = cache.sample(lat, lon)
                if mh is None:
                    continue
                feat_comaps.append(ca)
                feat_mh.append(mh)
                same_point_devs.append(abs(mh - ca))
                if abs(mh - ca) > args.threshold_m:
                    n_override += 1
                n_pts += 1
                if n_pts >= args.max_points_per_corridor:
                    break
            if len(feat_comaps) > 1:
                u, d = climb_descent(feat_comaps)
                c_up += u
                c_dn += d
                u, d = climb_descent(feat_mh)
                m_up += u
                m_dn += d
            if n_pts >= args.max_points_per_corridor:
                break
        same_point_devs.sort()
        p95 = same_point_devs[int(len(same_point_devs) * 0.95)] if same_point_devs else 0.0
        corridors.append(
            {
                "name": name,
                "points": n_pts,
                "override_rate": (n_override / n_pts) if n_pts else 0.0,
                "comaps_climb_m": round(c_up, 1),
                "comaps_descent_m": round(c_dn, 1),
                f"mapterhorn_z{args.zoom}_climb_m": round(m_up, 1),
                f"mapterhorn_z{args.zoom}_descent_m": round(m_dn, 1),
                "max_abs_same_point_deviation_m": round(max(same_point_devs) if same_point_devs else 0.0, 2),
                "median_same_point_deviation_m": round(
                    same_point_devs[len(same_point_devs) // 2] if same_point_devs else 0.0, 2
                ),
                "p95_same_point_deviation_m": round(p95, 2),
                "climb_delta_m": round(m_up - c_up, 1),
                "descent_delta_m": round(m_dn - c_dn, 1),
            }
        )

    report = {
        "zoom": args.zoom,
        "threshold_m": args.threshold_m,
        "method": "same-point: Mapterhorn sampled at dump_mwm_roads lat/lon; CoMaps alt_m from altitudes section",
        "prior_bug": (
            "Old corridor tool sampled Mapterhorn at densified corridor points and "
            "compared to CoMaps altitude of nearest road (up to max_snap_m). That "
            "spatial mismatch produced ~300 m fake deviations while leaf override "
            "rate at 2 m stayed ~1%."
        ),
        "units": {
            "comaps_altitudes": "geometry::Altitude int16 metres absolute (dump field alt_m)",
            "mapterhorn_terrarium": "metres = R*256 + G + B/256 - 32768",
            "sidecar_overrides": "integer decimetres (altitude_dm = round(metres * 10))",
        },
        "roads": [str(p) for p in args.roads_jsonl],
        "wall_s": round(time.monotonic() - t0, 1),
        "spots": spot_rows,
        "corridors": corridors,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
