#!/usr/bin/env python3
"""Bind wetland Soft/Hard to MWM road segments using geometry dumped from the MWM.

No planet/continental PBF. Inputs are dump_mwm_roads JSONL:
  --roads-jsonl     routable road features (full-resolution points)
  --wetlands-jsonl  wetland area triangles + Soft/Hard class

Uses a uniform lon/lat grid index over triangle bboxes (no shapely Polygon
objects — 4M+ triangles would OOM). Exact-tests segment–triangle intersection
and fully-inside. Emits both directions.

Boardwalk carve-out (PBF-free proxy):
  CoMaps has no bridge=boardwalk / surface=wood type (mapcss-mapping.csv only
  emits highway-{footway,path,track,pedestrian}-bridge for bridge=yes). Treat
  those bridge types as boardwalks when the segment touches/crosses wetland —
  exempt from Soft/Hard wetland penalty.
Policy: Soft=bog/fen/untyped; Hard=swamp/reedbed; marsh omitted at dump.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

SOFT = 1
HARD = 2

# ~1.1 km cells at mid-latitudes; fine enough for candidate pruning.
GRID_DEG = 0.01

# CoMaps types.txt / classificator: highway-{footway,path,track,pedestrian}|bridge
BOARDWALK_BRIDGE_TYPES = frozenset(
    {
        "highway-footway-bridge",
        "highway-path-bridge",
        "highway-track-bridge",
        "highway-pedestrian-bridge",
    }
)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def is_boardwalk_types(types: list[str]) -> bool:
    """True if feature types encode an explicit boardwalk or foot/path bridge."""
    normalized = []
    for raw in types:
        t = str(raw).strip().lower().replace("|", "-")
        normalized.append(t)
    joined = " ".join(normalized)
    if "boardwalk" in joined or "surface-wood" in joined:
        return True
    return any(t in BOARDWALK_BRIDGE_TYPES for t in normalized)


def orient(ax, ay, bx, by, cx, cy) -> float:
    return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)


def point_in_triangle(px, py, tri) -> bool:
    (x0, y0), (x1, y1), (x2, y2) = tri
    o1 = orient(x0, y0, x1, y1, px, py)
    o2 = orient(x1, y1, x2, y2, px, py)
    o3 = orient(x2, y2, x0, y0, px, py)
    has_neg = (o1 < 0) or (o2 < 0) or (o3 < 0)
    has_pos = (o1 > 0) or (o2 > 0) or (o3 > 0)
    return not (has_neg and has_pos)


def segments_intersect(a, b, c, d) -> bool:
    ax, ay = a
    bx, by = b
    cx, cy = c
    dx, dy = d
    o1 = orient(ax, ay, bx, by, cx, cy)
    o2 = orient(ax, ay, bx, by, dx, dy)
    o3 = orient(cx, cy, dx, dy, ax, ay)
    o4 = orient(cx, cy, dx, dy, bx, by)
    return ((o1 > 0) != (o2 > 0)) and ((o3 > 0) != (o4 > 0))


def segment_hits_triangle(p0, p1, tri) -> bool:
    if point_in_triangle(p0[0], p0[1], tri) or point_in_triangle(p1[0], p1[1], tri):
        return True
    mid = ((p0[0] + p1[0]) * 0.5, (p0[1] + p1[1]) * 0.5)
    if point_in_triangle(mid[0], mid[1], tri):
        return True
    a, b, c = tri
    return (
        segments_intersect(p0, p1, a, b)
        or segments_intersect(p0, p1, b, c)
        or segments_intersect(p0, p1, c, a)
    )


def cell(lon: float, lat: float) -> tuple[int, int]:
    return (int(math.floor(lon / GRID_DEG)), int(math.floor(lat / GRID_DEG)))


def cells_for_bbox(minx, miny, maxx, maxy):
    x0, x1 = cell(minx, miny)[0], cell(maxx, maxy)[0]
    y0, y1 = cell(minx, miny)[1], cell(maxx, maxy)[1]
    for gx in range(x0, x1 + 1):
        for gy in range(y0, y1 + 1):
            yield (gx, gy)


def load_wetland_index(path: Path):
    """Grid of triangle indices; triangles stored as float tuples (lon,lat)."""
    tris: list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = []
    classes: list[int] = []
    bboxes: list[tuple[float, float, float, float]] = []
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)

    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            feat = json.loads(line)
            cls = int(feat["class"])
            if cls not in (SOFT, HARD):
                continue
            for raw in feat.get("triangles") or []:
                if len(raw) != 3:
                    continue
                t = (
                    (float(raw[0][1]), float(raw[0][0])),
                    (float(raw[1][1]), float(raw[1][0])),
                    (float(raw[2][1]), float(raw[2][0])),
                )
                xs = (t[0][0], t[1][0], t[2][0])
                ys = (t[0][1], t[1][1], t[2][1])
                minx, maxx = min(xs), max(xs)
                miny, maxy = min(ys), max(ys)
                if maxx < minx or maxy < miny:
                    continue
                idx = len(tris)
                tris.append(t)
                classes.append(cls)
                bboxes.append((minx, miny, maxx, maxy))
                for c in cells_for_bbox(minx, miny, maxx, maxy):
                    grid[c].append(idx)
    return grid, tris, classes, bboxes


def candidate_indices(grid, p0, p1):
    minx = min(p0[0], p1[0])
    maxx = max(p0[0], p1[0])
    miny = min(p0[1], p1[1])
    maxy = max(p0[1], p1[1])
    seen: set[int] = set()
    out: list[int] = []
    for c in cells_for_bbox(minx, miny, maxx, maxy):
        for idx in grid.get(c, ()):
            if idx not in seen:
                seen.add(idx)
                out.append(idx)
    return out


def bbox_overlaps(bb, p0, p1) -> bool:
    minx, miny, maxx, maxy = bb
    sx0, sx1 = (p0[0], p1[0]) if p0[0] <= p1[0] else (p1[0], p0[0])
    sy0, sy1 = (p0[1], p1[1]) if p0[1] <= p1[1] else (p1[1], p0[1])
    return not (sx1 < minx or sx0 > maxx or sy1 < miny or sy0 > maxy)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--roads-jsonl", type=Path, required=True)
    ap.add_argument("--wetlands-jsonl", type=Path, required=True)
    ap.add_argument("--out-wetland", type=Path, required=True)
    ap.add_argument("--out-boardwalk", type=Path, required=True)
    ap.add_argument("--no-both-directions", action="store_true")
    args = ap.parse_args()
    both_dirs = not args.no_both_directions

    grid, tris, classes, bboxes = load_wetland_index(args.wetlands_jsonl)

    soft_segments = 0
    hard_segments = 0
    road_features = 0
    road_segments = 0
    road_length_m = 0.0
    wet_length_m = 0.0
    soft_length_m = 0.0
    hard_length_m = 0.0
    boardwalk_segments = 0

    records: dict[tuple[int, int, int], int] = {}
    bw_keys: set[tuple[int, int, int]] = set()

    with args.roads_jsonl.open() as f:
        for line in f:
            if not line.strip():
                continue
            feat = json.loads(line)
            fid = int(feat["feature_id"])
            pts = feat.get("points") or []
            types = list(feat.get("types") or [])
            bw = is_boardwalk_types(types)
            if len(pts) < 2:
                continue
            road_features += 1
            for seg_i in range(len(pts) - 1):
                lat0 = float(pts[seg_i]["lat"])
                lon0 = float(pts[seg_i]["lon"])
                lat1 = float(pts[seg_i + 1]["lat"])
                lon1 = float(pts[seg_i + 1]["lon"])
                seg_m = haversine_m(lat0, lon0, lat1, lon1)
                road_segments += 1
                road_length_m += seg_m
                p0 = (lon0, lat0)
                p1 = (lon1, lat1)

                best = None
                for idx in candidate_indices(grid, p0, p1):
                    if not bbox_overlaps(bboxes[idx], p0, p1):
                        continue
                    if not segment_hits_triangle(p0, p1, tris[idx]):
                        continue
                    cls = classes[idx]
                    if best is None or cls > best:
                        best = cls

                if best is None:
                    continue

                # Wetland-touching foot/path/track/pedestrian bridges → boardwalk
                # carve-out (exempt from Soft/Hard penalty). Bridges that do not
                # touch wetland are left alone (not boardwalks).
                if bw:
                    boardwalk_segments += 1
                    dirs = (0, 1) if both_dirs else (0,)
                    for d in dirs:
                        bw_keys.add((fid, seg_i, d))
                    continue

                wet_length_m += seg_m
                if best == SOFT:
                    soft_segments += 1
                    soft_length_m += seg_m
                else:
                    hard_segments += 1
                    hard_length_m += seg_m
                dirs = (0, 1) if both_dirs else (0,)
                for d in dirs:
                    key = (fid, seg_i, d)
                    prev = records.get(key)
                    if prev is None or best > prev:
                        records[key] = best

    wetland_out = [
        {
            "feature_id": fid,
            "segment_index": seg,
            "direction": direction,
            "class": cls,
        }
        for (fid, seg, direction), cls in sorted(records.items())
    ]
    boardwalk_out = [
        {
            "feature_id": fid,
            "segment_index": seg,
            "direction": direction,
            "flag": 1,
        }
        for fid, seg, direction in sorted(bw_keys)
    ]

    args.out_wetland.parent.mkdir(parents=True, exist_ok=True)
    with args.out_wetland.open("w") as f:
        for r in wetland_out:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    with args.out_boardwalk.open("w") as f:
        for r in boardwalk_out:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")

    soft_records = sum(1 for r in wetland_out if r["class"] == SOFT)
    hard_records = sum(1 for r in wetland_out if r["class"] == HARD)
    stats = {
        "bind_mode": "mwm_geometry",
        "boardwalk_proxy": "highway-{footway,path,track,pedestrian}-bridge ∩ wetland",
        "wetland_records": len(wetland_out),
        "soft_records": soft_records,
        "hard_records": hard_records,
        "boardwalk_records": len(boardwalk_out),
        "soft_segments": soft_segments,
        "hard_segments": hard_segments,
        "boardwalk_segments": boardwalk_segments,
        "road_features": road_features,
        "road_segments": road_segments,
        "road_length_m": round(road_length_m, 1),
        "wet_length_m": round(wet_length_m, 1),
        "soft_length_m": round(soft_length_m, 1),
        "hard_length_m": round(hard_length_m, 1),
        "wet_length_share": (wet_length_m / road_length_m) if road_length_m else 0.0,
        "wetland_triangles": len(tris),
        "out_wetland": str(args.out_wetland),
        "out_boardwalk": str(args.out_boardwalk),
    }
    print(json.dumps(stats), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
