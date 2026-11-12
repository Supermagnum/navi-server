#!/usr/bin/env python3
"""One-off Østlandet boardwalk recall: OSM ground truth vs MWM wetland∩bridge proxy.

Production path stays PBF-free; this script is validation-only.
OSM boardwalk truth (any of):
  - bridge=boardwalk
  - footway=boardwalk / highway=footway + bridge=boardwalk
  - highway in {footway,path,track,pedestrian} + bridge=yes + surface=wood
Proxy truth: boardwalk.jsonl from annotate-wetland-from-mwm (bridge∩wetland).
Match: OSM way midpoint within --match-m of a proxy segment midpoint.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def osmium_boardwalk_geojson(pbf: Path, out: Path) -> None:
    """Export candidate boardwalk ways via osmium tags-filter + export."""
    filt = out.with_suffix(".filtered.osm.pbf")
    # Broad filter then classify in Python (osmium expressions are limited).
    subprocess.run(
        [
            "osmium",
            "tags-filter",
            str(pbf),
            "w/bridge=boardwalk",
            "w/footway=boardwalk",
            "w/highway=footway",
            "w/highway=path",
            "w/highway=track",
            "w/highway=pedestrian",
            "-o",
            str(filt),
            "--overwrite",
        ],
        check=True,
    )
    subprocess.run(
        [
            "osmium",
            "export",
            str(filt),
            "-f",
            "geojson",
            "-o",
            str(out),
            "--overwrite",
            "--geometry-types=linestring",
        ],
        check=True,
    )


def is_osm_boardwalk(props: dict) -> bool:
    tags = {k.lower(): str(v).lower() for k, v in props.items()}
    if tags.get("bridge") == "boardwalk" or tags.get("footway") == "boardwalk":
        return True
    hw = tags.get("highway", "")
    if hw in {"footway", "path", "track", "pedestrian"}:
        if tags.get("bridge") in {"yes", "boardwalk", "true", "1"} and tags.get(
            "surface"
        ) in {"wood", "wooden"}:
            return True
    return False


def linestring_mid(coords: list) -> tuple[float, float] | None:
    if len(coords) < 2:
        return None
    # coords are [lon, lat]
    mid_i = len(coords) // 2
    lon, lat = coords[mid_i][0], coords[mid_i][1]
    return float(lat), float(lon)


def load_proxy_mids(roads_paths: list[Path], boardwalk_paths: list[Path]):
    """Return list of (lat, lon) midpoints for boardwalk-flagged segments."""
    bw_keys: set[tuple[int, int]] = set()
    for path in boardwalk_paths:
        with path.open() as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                bw_keys.add((int(r["feature_id"]), int(r["segment_index"])))
    mids = []
    for path in roads_paths:
        with path.open() as f:
            for line in f:
                if not line.strip():
                    continue
                feat = json.loads(line)
                fid = int(feat["feature_id"])
                pts = feat.get("points") or []
                for seg_i in range(len(pts) - 1):
                    if (fid, seg_i) not in bw_keys:
                        continue
                    a, b = pts[seg_i], pts[seg_i + 1]
                    mids.append(
                        (
                            (float(a["lat"]) + float(b["lat"])) * 0.5,
                            (float(a["lon"]) + float(b["lon"])) * 0.5,
                        )
                    )
    return mids, len(bw_keys)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pbf", type=Path, required=True)
    ap.add_argument("--roads-jsonl", type=Path, nargs="+", required=True)
    ap.add_argument("--boardwalk-jsonl", type=Path, nargs="+", required=True)
    ap.add_argument("--match-m", type=float, default=75.0)
    ap.add_argument("--out-json", type=Path, required=True)
    args = ap.parse_args()

    proxy_mids, n_bw_keys = load_proxy_mids(args.roads_jsonl, args.boardwalk_jsonl)

    with tempfile.TemporaryDirectory() as td:
        gj = Path(td) / "boardwalks.geojson"
        osmium_boardwalk_geojson(args.pbf, gj)
        data = json.loads(gj.read_text())

    osm_mids = []
    for feat in data.get("features") or []:
        props = feat.get("properties") or {}
        if not is_osm_boardwalk(props):
            continue
        geom = feat.get("geometry") or {}
        if geom.get("type") != "LineString":
            continue
        mid = linestring_mid(geom.get("coordinates") or [])
        if mid is None:
            continue
        osm_mids.append(mid)

    caught = 0
    missed = 0
    for lat, lon in osm_mids:
        hit = any(haversine_m(lat, lon, plat, plon) <= args.match_m for plat, plon in proxy_mids)
        if hit:
            caught += 1
        else:
            missed += 1

    # False positives: proxy midpoints with no OSM boardwalk within match_m
    fp = 0
    for plat, plon in proxy_mids:
        if not any(haversine_m(olat, olon, plat, plon) <= args.match_m for olat, olon in osm_mids):
            fp += 1

    report = {
        "osm_boardwalk_ways": len(osm_mids),
        "proxy_boardwalk_segments_unique": n_bw_keys,
        "proxy_midpoints": len(proxy_mids),
        "match_m": args.match_m,
        "caught": caught,
        "missed": missed,
        "false_positives": fp,
        "recall": (caught / len(osm_mids)) if osm_mids else None,
        "precision": (caught / (caught + fp)) if (caught + fp) else None,
        "note": (
            "Proxy = highway-{footway,path,track,pedestrian}-bridge ∩ wetland. "
            "OSM boardwalks on dry ground or without bridge=yes are expected misses."
        ),
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
