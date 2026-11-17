#!/usr/bin/env python3
"""Bind OSM vehicle-restriction tags to MWM road segments.

Reads a filtered Geofabrik extract (or GeoJSON of tagged highway ways) and a
``dump_mwm_roads`` JSONL, then emits vehicle_limits JSONL for navi-mwm-sidecar.

Match rule (never guessed):
  * highway class (from OSM ``highway=*`` vs MWM ``highway-*`` type)
  * name when both sides have a non-empty name (case-insensitive)
  * a segment is kept only when **both** endpoints lie on the tagged way
    (quantised node coordinates, same quantum as geometry_cache)

Unmatched tagged ways are listed in the report; they are never invented.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from geometry_cache import QUANTUM_DEG, quantise_coord  # noqa: E402
from vehicle_limit_units import (  # noqa: E402
    metres_to_cm,
    parse_length_metres,
    parse_weight_tonnes,
    tonnes_to_10kg,
)

# Plain keys only; :conditional / :physical / :hgv variants are out of scope.
LIMIT_KEYS = (
    "maxheight",
    "maxwidth",
    "maxlength",
    "maxweight",
    "maxaxleload",
    "maxbogieweight",
)


def _highway_class(tags_or_types) -> str | None:
    if isinstance(tags_or_types, dict):
        hw = tags_or_types.get("highway")
        return hw.strip().lower() if hw else None
    for t in tags_or_types or []:
        if isinstance(t, str) and t.startswith("highway-"):
            return t[len("highway-") :].lower()
    return None


def _name(tags_or_props) -> str:
    if isinstance(tags_or_props, dict):
        for k in ("name", "bridge:name", "ref"):
            v = tags_or_props.get(k)
            if v and str(v).strip():
                return str(v).strip().lower()
    return ""


def _qpair(lat: float, lon: float) -> tuple[int, int]:
    return quantise_coord(lat), quantise_coord(lon)


def _parse_tag(key: str, raw: str | None, kind: str) -> float | None:
    """Parse one tag; log and skip unparseable non-empty values."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if kind == "length":
        v = parse_length_metres(s)
    else:
        v = parse_weight_tonnes(s)
    if v is None:
        # default/none/unsigned and conditionals are silent skips; others log.
        low = s.lower()
        if low not in {
            "default",
            "none",
            "unsigned",
            "below_default",
            "no",
            "yes",
            "unknown",
            "unlimited",
        } and "@" not in s and ";" not in s:
            print(
                json.dumps({"skip_unparseable": True, "key": key, "value": s}),
                file=sys.stderr,
                flush=True,
            )
        return None
    return v


def limits_from_tags(tags: dict) -> dict[str, int] | None:
    h = _parse_tag("maxheight", tags.get("maxheight"), "length")
    w = _parse_tag("maxwidth", tags.get("maxwidth"), "length")
    length = _parse_tag("maxlength", tags.get("maxlength"), "length")
    wt = _parse_tag("maxweight", tags.get("maxweight"), "weight")
    ax = _parse_tag("maxaxleload", tags.get("maxaxleload"), "weight")
    bg = _parse_tag("maxbogieweight", tags.get("maxbogieweight"), "weight")
    out = {
        "max_height_cm": metres_to_cm(h) if h is not None else 0,
        "max_width_cm": metres_to_cm(w) if w is not None else 0,
        "max_length_cm": metres_to_cm(length) if length is not None else 0,
        "max_weight_10kg": tonnes_to_10kg(wt) if wt is not None else 0,
        "max_axleload_10kg": tonnes_to_10kg(ax) if ax is not None else 0,
        "max_bogie_10kg": tonnes_to_10kg(bg) if bg is not None else 0,
    }
    if all(v == 0 for v in out.values()):
        return None
    return out


def load_tagged_ways_geojson(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ways = []
    for feat in data.get("features", []):
        props = feat.get("properties") or {}
        tags = {k: v for k, v in props.items() if k in LIMIT_KEYS or k in ("highway", "name", "bridge:name", "ref", "@id", "id", "osm_id")}
        if "highway" not in tags and props.get("highway"):
            tags["highway"] = props["highway"]
        geom = feat.get("geometry") or {}
        if geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        nodes = [(float(c[1]), float(c[0])) for c in coords]  # lat, lon
        wid = props.get("@id") or props.get("id") or props.get("osm_id")
        if isinstance(wid, str) and wid.startswith("way/"):
            wid = int(wid.split("/", 1)[1])
        ways.append(
            {
                "osm_way_id": int(wid) if wid is not None else None,
                "tags": tags,
                "nodes": nodes,
                "name": _name(tags),
                "highway": _highway_class(tags),
            }
        )
    return ways


def _have_osmium_cli() -> bool:
    from shutil import which

    return which("osmium") is not None


def filter_pbf_to_geojson_pyosmium(
    pbf: Path,
    out_geojson: Path,
    bbox: tuple[float, float, float, float] | None,
) -> None:
    """Stream tagged highway ways via pyosmium (no intermediate PBF kept)."""
    import osmium

    limit_keys = set(LIMIT_KEYS)

    class Handler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.features: list[dict] = []

        def way(self, w: osmium.osm.Way) -> None:  # noqa: N802 — osmium API
            tags = {t.k: t.v for t in w.tags}
            if "highway" not in tags:
                return
            if not any(k in tags for k in limit_keys):
                return
            if bbox is not None:
                west, south, east, north = bbox
                # Keep way if any node falls inside the leaf bbox (generous).
                inside = False
                for n in w.nodes:
                    if not n.location.valid():
                        continue
                    lon, lat = float(n.location.lon), float(n.location.lat)
                    if west <= lon <= east and south <= lat <= north:
                        inside = True
                        break
                if not inside:
                    return
            coords = []
            for n in w.nodes:
                if not n.location.valid():
                    continue
                coords.append([float(n.location.lon), float(n.location.lat)])
            if len(coords) < 2:
                return
            props = {k: tags[k] for k in tags if k in limit_keys or k in ("highway", "name", "bridge:name", "ref")}
            props["@id"] = f"way/{w.id}"
            self.features.append(
                {
                    "type": "Feature",
                    "properties": props,
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
            )

    h = Handler()
    # locations=True so node coords are available on ways.
    h.apply_file(str(pbf), locations=True, idx="flex_mem")
    out_geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": h.features}, ensure_ascii=False),
        encoding="utf-8",
    )


def filter_pbf_to_geojson(pbf: Path, out_geojson: Path, bbox: tuple[float, float, float, float] | None) -> None:
    """Stream-filter a PBF to tagged highway ways, then export GeoJSON. Deletes intermediates."""
    if not _have_osmium_cli():
        filter_pbf_to_geojson_pyosmium(pbf, out_geojson, bbox)
        return
    with tempfile.TemporaryDirectory(prefix="navi-vl-") as td:
        td_path = Path(td)
        work = pbf
        clipped = td_path / "clip.pbf"
        if bbox is not None:
            west, south, east, north = bbox
            subprocess.check_call(
                [
                    "osmium",
                    "extract",
                    f"--bbox={west},{south},{east},{north}",
                    "-o",
                    str(clipped),
                    "--overwrite",
                    str(pbf),
                ]
            )
            work = clipped
        filtered = td_path / "limits.pbf"
        # Keep ways that have any limit tag, then restrict to highway=*.
        subprocess.check_call(
            [
                "osmium",
                "tags-filter",
                "-o",
                str(filtered),
                "--overwrite",
                str(work),
                "w/maxheight",
                "w/maxwidth",
                "w/maxlength",
                "w/maxweight",
                "w/maxaxleload",
                "w/maxbogieweight",
            ]
        )
        filtered2 = td_path / "limits_hw.pbf"
        subprocess.check_call(
            [
                "osmium",
                "tags-filter",
                "-o",
                str(filtered2),
                "--overwrite",
                str(filtered),
                "w/highway",
            ]
        )
        subprocess.check_call(
            [
                "osmium",
                "export",
                "-f",
                "geojson",
                "-o",
                str(out_geojson),
                "--overwrite",
                str(filtered2),
            ]
        )


def load_roads_jsonl(path: Path) -> list[dict]:
    roads = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            feat = json.loads(line)
            pts = feat.get("points") or []
            if len(pts) < 2:
                continue
            roads.append(
                {
                    "feature_id": int(feat["feature_id"]),
                    "points": [(float(p["lat"]), float(p["lon"])) for p in pts],
                    "highway": _highway_class(feat.get("types")),
                    "name": _name(feat.get("tags") or feat),
                    "types": feat.get("types") or [],
                }
            )
    return roads


def match_way_to_roads(way: dict, roads: list[dict]) -> list[dict]:
    """Return vehicle_limits JSONL rows for one OSM way, or empty if unmatched."""
    lim = limits_from_tags(way["tags"])
    if lim is None:
        return []
    way_nodes = {_qpair(lat, lon) for lat, lon in way["nodes"]}
    hw = way["highway"]
    wname = way["name"]
    out = []
    for road in roads:
        if hw and road["highway"] and hw != road["highway"]:
            continue
        rname = road["name"]
        if wname and rname and wname != rname:
            continue
        pts = road["points"]
        for seg_i in range(len(pts) - 1):
            a = _qpair(*pts[seg_i])
            b = _qpair(*pts[seg_i + 1])
            if a in way_nodes and b in way_nodes:
                row = {
                    "feature_id": road["feature_id"],
                    "segment_index": seg_i,
                    **lim,
                    "osm_way_id": way["osm_way_id"],
                }
                out.append(row)
    return out


def annotate(
    ways: list[dict],
    roads: list[dict],
) -> tuple[list[dict], dict]:
    records: list[dict] = []
    matched_ways = 0
    skipped_parse = 0
    unparseable: list[dict] = []
    unmatched: list[dict] = []
    for way in ways:
        raw_tags = {k: way["tags"].get(k) for k in LIMIT_KEYS if way["tags"].get(k)}
        if limits_from_tags(way["tags"]) is None:
            skipped_parse += 1
            entry = {
                "osm_way_id": way["osm_way_id"],
                "name": way["name"],
                "highway": way["highway"],
                "tags": raw_tags,
            }
            unparseable.append(entry)
            print(
                json.dumps({"vehicle_limits_unparseable": entry}, ensure_ascii=False),
                file=sys.stderr,
                flush=True,
            )
            continue
        rows = match_way_to_roads(way, roads)
        if not rows:
            unmatched.append(
                {
                    "osm_way_id": way["osm_way_id"],
                    "name": way["name"],
                    "highway": way["highway"],
                    "tags": raw_tags,
                }
            )
            continue
        matched_ways += 1
        records.extend(rows)
    # Dedup by (feature_id, segment_index); last writer wins (same limits expected).
    by_key: dict[tuple[int, int], dict] = {}
    for r in records:
        by_key[(int(r["feature_id"]), int(r["segment_index"]))] = r
    deduped = list(by_key.values())
    deduped.sort(key=lambda r: (r["feature_id"], r["segment_index"]))
    parseable = max(1, len(ways) - skipped_parse)
    report = {
        "tagged_ways": len(ways),
        "skipped_unparseable": skipped_parse,
        "matched_ways": matched_ways,
        "unmatched_ways": len(unmatched),
        "match_rate": matched_ways / parseable,
        "records": len(deduped),
        "unparseable": unparseable,
        "unmatched": unmatched,
    }
    return deduped, report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--roads-jsonl", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--report", type=Path, default=None)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--geojson", type=Path, help="Pre-exported tagged ways GeoJSON")
    src.add_argument("--pbf", type=Path, help="Geofabrik extract; filtered then deleted")
    ap.add_argument(
        "--bbox",
        type=str,
        default=None,
        help="west,south,east,north for --pbf clip (leaf border)",
    )
    args = ap.parse_args()

    if args.geojson:
        ways = load_tagged_ways_geojson(args.geojson)
    else:
        bbox = None
        if args.bbox:
            parts = [float(x) for x in args.bbox.split(",")]
            if len(parts) != 4:
                raise SystemExit("--bbox needs west,south,east,north")
            bbox = (parts[0], parts[1], parts[2], parts[3])
        with tempfile.NamedTemporaryFile(suffix=".geojson", delete=False) as tmp:
            gj = Path(tmp.name)
        try:
            filter_pbf_to_geojson(args.pbf, gj, bbox)
            ways = load_tagged_ways_geojson(gj)
        finally:
            gj.unlink(missing_ok=True)

    roads = load_roads_jsonl(args.roads_jsonl)
    records, report = annotate(ways, roads)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = {
        k: report[k]
        for k in report
        if k not in ("unmatched", "unparseable")
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
