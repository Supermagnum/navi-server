#!/usr/bin/env python3
"""Annotate MWM road segments that cross/enter wetlands (Navi Soft/Hard policy).

Soft = bog/string_bog/fen/untyped wetland; Hard = swamp/reedbed; marsh omitted.
Boardwalk carve-out: bridge=boardwalk or surface=wood → boardwalk flag, skip Hard.

Emits JSONL records for navi-mwm-sidecar bake. Marks ambiguous osm2ft bindings
with \"ambiguous\": true when a way maps to multiple feature ids.
"""
from __future__ import annotations

import argparse
import json
import struct
import subprocess
import tempfile
from pathlib import Path

SOFT = {"bog", "string_bog", "fen"}
HARD = {"swamp", "reedbed"}


def classify(tags: dict) -> int | None:
    w = tags.get("wetland")
    if w:
        v = w.strip().lower()
        if v in SOFT:
            return 1
        if v in HARD:
            return 2
        return None
    if tags.get("natural", "").lower() == "wetland":
        return 1
    return None


def is_boardwalk(tags: dict) -> bool:
    return tags.get("bridge", "").lower() == "boardwalk" or tags.get("surface", "").lower() == "wood"


def read_varuint(buf: bytes, off: int) -> tuple[int, int]:
    res = 0
    shift = 0
    while True:
        byte = buf[off]
        off += 1
        res |= (byte & 0x7F) << shift
        if byte < 0x80:
            return res, off
        shift += 7


def load_osm2ft_ways(path: Path) -> dict[int, list[int]]:
    b = path.read_bytes()
    assert struct.unpack_from("<I", b, 0)[0] == 0xFFFFFFFF
    count, off = read_varuint(b, 5)
    out: dict[int, list[int]] = {}
    for i in range(count):
        main, _add, fid = struct.unpack_from("<QQI", b, off + i * 24)
        if ((main >> 56) & 0xFF) != 0x80:
            continue
        serial = main & ((1 << 48) - 1)
        out.setdefault(serial, []).append(fid)
    return out


def osmium_export_geojson(pbf: Path, out: Path, expr: str, bbox: tuple[float, float, float, float]):
    west, south, east, north = bbox
    filtered = out.with_suffix(".filtered.pbf")
    subprocess.check_call(
        [
            "osmium",
            "tags-filter",
            "-o",
            str(filtered),
            "--overwrite",
            str(pbf),
            expr,
            f"--bbox={west},{south},{east},{north}",
        ]
    )
    subprocess.check_call(
        [
            "osmium",
            "export",
            "-f",
            "geojson",
            "-o",
            str(out),
            "--overwrite",
            str(filtered),
        ]
    )


def poly_bbox(path: Path) -> tuple[float, float, float, float]:
    min_lon = min_lat = float("inf")
    max_lon = max_lat = float("-inf")
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        min_lon = min(min_lon, lon)
        max_lon = max(max_lon, lon)
        min_lat = min(min_lat, lat)
        max_lat = max(max_lat, lat)
    return min_lon, min_lat, max_lon, max_lat


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pbf", required=True, type=Path)
    ap.add_argument("--osm2ft", required=True, type=Path)
    ap.add_argument("--border-poly", type=Path, default=None)
    ap.add_argument("--bbox", default=None, help="west,south,east,north")
    ap.add_argument("--out-wetland", required=True, type=Path)
    ap.add_argument("--out-boardwalk", required=True, type=Path)
    args = ap.parse_args()

    from shapely.geometry import LineString, Point, shape
    from shapely.strtree import STRtree

    if args.bbox:
        bbox = tuple(float(x) for x in args.bbox.split(","))  # type: ignore
    elif args.border_poly:
        w, s, e, n = poly_bbox(args.border_poly)
        pad = 0.02
        bbox = (w - pad, s - pad, e + pad, n + pad)
    else:
        raise SystemExit("need --bbox or --border-poly")

    osm2ft = load_osm2ft_ways(args.osm2ft)

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        wet_gj = td_path / "wetland.geojson"
        hwy_gj = td_path / "highway.geojson"
        osmium_export_geojson(args.pbf, wet_gj, "n/w/r/natural=wetland", bbox)  # type: ignore
        osmium_export_geojson(args.pbf, hwy_gj, "w/highway", bbox)  # type: ignore

        wet_feats = json.loads(wet_gj.read_text()).get("features", [])
        polys = []
        classes = []
        for f in wet_feats:
            tags = f.get("properties") or {}
            cls = classify(tags)
            if cls is None:
                continue
            geom = shape(f["geometry"])
            if geom.is_empty:
                continue
            if geom.geom_type == "Polygon":
                polys.append(geom)
                classes.append(cls)
            elif geom.geom_type == "MultiPolygon":
                for g in geom.geoms:
                    polys.append(g)
                    classes.append(cls)
        tree = STRtree(polys) if polys else None

        hwy_feats = json.loads(hwy_gj.read_text()).get("features", [])
        wetland_out = []
        boardwalk_out = []
        ambiguous = 0
        for f in hwy_feats:
            props = f.get("properties") or {}
            osm_id = props.get("@id") or props.get("id")
            if osm_id is None:
                continue
            try:
                wid = int(str(osm_id).split("/")[-1])
            except ValueError:
                continue
            geom = shape(f["geometry"])
            if geom.is_empty or geom.geom_type not in ("LineString", "MultiLineString"):
                continue
            lines = [geom] if geom.geom_type == "LineString" else list(geom.geoms)
            fids = osm2ft.get(wid, [])
            amb = len(fids) > 1
            if amb:
                ambiguous += 1
            if not fids:
                continue
            bw = is_boardwalk(props)
            for line in lines:
                coords = list(line.coords)
                for seg_i in range(max(0, len(coords) - 1)):
                    (x0, y0) = coords[seg_i][:2]
                    (x1, y1) = coords[seg_i + 1][:2]
                    mid = Point((x0 + x1) / 2.0, (y0 + y1) / 2.0)
                    cls = None
                    if tree is not None:
                        for idx in tree.query(mid):
                            poly = polys[int(idx)]
                            if poly.contains(mid) or poly.intersects(LineString([(x0, y0), (x1, y1)])):
                                cls = classes[int(idx)]
                                break
                    for fid in fids:
                        if bw:
                            boardwalk_out.append(
                                {
                                    "feature_id": fid,
                                    "segment_index": seg_i,
                                    "direction": 0,
                                    "flag": 1,
                                    "ambiguous": amb,
                                }
                            )
                        if cls is not None:
                            if bw and cls == 2:
                                continue
                            wetland_out.append(
                                {
                                    "feature_id": fid,
                                    "segment_index": seg_i,
                                    "direction": 0,
                                    "class": cls,
                                    "ambiguous": amb,
                                }
                            )

    args.out_wetland.parent.mkdir(parents=True, exist_ok=True)
    with args.out_wetland.open("w") as f:
        for r in wetland_out:
            f.write(json.dumps(r) + "\n")
    with args.out_boardwalk.open("w") as f:
        for r in boardwalk_out:
            f.write(json.dumps(r) + "\n")
    print(
        json.dumps(
            {
                "wetland_records": len(wetland_out),
                "boardwalk_records": len(boardwalk_out),
                "ambiguous_ways": ambiguous,
                "out_wetland": str(args.out_wetland),
                "out_boardwalk": str(args.out_boardwalk),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
