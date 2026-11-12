#!/usr/bin/env python3
"""Bind OSM wetland annotations to CoMaps MWM feature ids via road midpoint match.

Takes osmium-exported highway GeoJSON (with wetland class already decided per segment
by annotate-wetland-edges.py) OR runs a simplified annotate using a roads dump from
dump_mwm_roads.

Preferred path for CoMaps release (no .osm2ft):
  1. annotate-wetland-edges.py --emit-osm-segments ... (OSM way id + segment + class)
  2. This script matches OSM way midpoints to nearest dumped MWM road feature.

For the Nordic pilot we reuse annotate-wetland-edges geometry logic but replace
osm2ft lookup with STRtree nearest-neighbour on dumped road midpoints.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def load_road_index(roads_jsonl: Path):
    from shapely.geometry import Point
    from shapely.strtree import STRtree

    mids = []
    fids = []
    with roads_jsonl.open() as f:
        for line in f:
            if not line.strip():
                continue
            feat = json.loads(line)
            pts = feat["points"]
            if not pts:
                continue
            mid = pts[len(pts) // 2]
            mids.append(Point(float(mid["lon"]), float(mid["lat"])))
            fids.append(int(feat["feature_id"]))
    tree = STRtree(mids)
    return tree, mids, fids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roads-jsonl", type=Path, required=True)
    ap.add_argument("--osm-wetland-jsonl", type=Path, required=True,
                    help="JSONL with osm_way_id, segment_index, direction, class, mid_lat, mid_lon")
    ap.add_argument("--out-wetland", type=Path, required=True)
    ap.add_argument("--out-boardwalk", type=Path, required=True)
    ap.add_argument("--max-match-m", type=float, default=75.0)
    args = ap.parse_args()

    tree, mids, fids = load_road_index(args.roads_jsonl)

    wetland_out = []
    boardwalk_out = []
    matched = 0
    unmatched = 0
    ambiguous = 0

    with args.osm_wetland_jsonl.open() as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            lat = float(row["mid_lat"])
            lon = float(row["mid_lon"])
            from shapely.geometry import Point

            pt = Point(lon, lat)
            # shapely 2: nearest
            try:
                idx = tree.nearest(pt)
            except Exception:
                # shapely 1.8
                idxs = tree.nearest(pt)
                idx = int(idxs) if not hasattr(idxs, "__iter__") else int(list(idxs)[0])
            if hasattr(idx, "item"):
                idx = int(idx.item())
            else:
                idx = int(idx)
            near = mids[idx]
            dist = haversine_m(lat, lon, near.y, near.x)
            if dist > args.max_match_m:
                unmatched += 1
                continue
            fid = fids[idx]
            matched += 1
            rec = {
                "feature_id": fid,
                "segment_index": int(row["segment_index"]),
                "direction": int(row.get("direction", 0)),
                "ambiguous": bool(row.get("ambiguous", False)),
            }
            if row.get("ambiguous"):
                ambiguous += 1
            kind = row.get("kind", "wetland")
            if kind == "boardwalk":
                rec["flag"] = int(row.get("flag", 1))
                boardwalk_out.append(rec)
            else:
                rec["class"] = int(row["class"])
                wetland_out.append(rec)

    with args.out_wetland.open("w") as f:
        for r in wetland_out:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    with args.out_boardwalk.open("w") as f:
        for r in boardwalk_out:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")

    print(
        json.dumps(
            {
                "matched": matched,
                "unmatched": unmatched,
                "ambiguous": ambiguous,
                "wetland_records": len(wetland_out),
                "boardwalk_records": len(boardwalk_out),
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
