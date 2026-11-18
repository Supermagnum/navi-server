#!/usr/bin/env python3
"""Bind OSM vehicle-restriction tags to MWM road segments.

Reads a filtered Geofabrik extract (or GeoJSON of tagged highway ways) and a
``dump_mwm_roads`` JSONL, then emits vehicle_limits JSONL for navi-mwm-sidecar.

Match rule (tolerance-based; never invents limits):
  * A segment is kept when **both** endpoints lie within TOLERANCE_M of the
    tagged OSM way polyline, the highway class is compatible, and false-
    positive guards pass (bearing + bridge/tunnel layer).
  * Name is a tie-breaker when several roads claim the same segment; it is
    never required.
  * Unmatched tagged ways are listed in the report with a reason.

Class mapping: OSM ``highway=*`` vs MWM ``highway-<class>`` / ``highway-<class>-bridge``
/ ``highway-<class>-tunnel`` (and compound stems such as ``service-driveway``).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from geometry_cache import QUANTUM_DEG, quantise_coord  # noqa: E402
from vehicle_limit_units import (  # noqa: E402
    classify_tag_value,
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

# Diagnosis (Norway_Hedmark): CoMaps endpoints sit 0.4–5.6 m from OSM nodes
# (median ~2.8 m, p90 ~5.2 m). Known bridges Stai/Atna need ~4.4 m; several
# true bridges (Evenstad, Estensmo, Plassen, Lera bridge feature) sit at
# 5.5–5.9 m. Exact quantum membership matched 1/821 ways. 8 m covers those
# bridges (Hedmark → 95.5%) while staying below typical parallel-road
# spacing (~10–15 m).
TOLERANCE_M = 8.0
# Bearing guard: reject segments whose direction differs by more than this
# from the nearest OSM way segment (also accepts reverse bearing). CoMaps
# short tunnels/bridges often snap to 0/90 deg; 45 deg + degenerate skip.
MAX_BEARING_DIFF_DEG = 45.0
# Skip bearing when MWM segment is shorter than this (quantised stubs).
MIN_BEARING_SEGMENT_M = 2.0
# Along-way overlap: projected span on the OSM way must cover this fraction of
# min(segment_length, osm_way_length). Using min() so a short tagged stretch
# (bridge) is not rejected when the MWM segment is longer than the way.
# Nordic short-side FP audit: perpendicular junction stubs have ratio ~0;
# well-aligned main segments have p5 ~0.80. 0.70 clears stubs while keeping
# Hedmark/Western/Hordaland ≥ 96%.
OVERLAP_MIN_RATIO = 0.70
# Short tagged OSM ways (typical Nordic bridges): require covering most of the
# way itself rather than the longer MWM chord. Still high enough that
# perpendicular junction stubs (ratio ~0) fail.
OVERLAP_MIN_RATIO_SHORT_WAY = 0.50
SHORT_WAY_OVERLAP_M = 40.0
# Below this length, overlap is ill-defined (duplicate quantised points).
OVERLAP_DEG_EPS_M = 0.5
# Spatial index cell size in quanta (~1.1 m each at equator).
GRID_CELL_QUANTA = 40

# OSM highway class → acceptable MWM base stems (after -bridge/-tunnel strip).
HIGHWAY_ALIASES: dict[str, frozenset[str]] = {
    "cycleway": frozenset({"cycleway", "footway-bicycle", "path-bicycle"}),
    "footway": frozenset({"footway", "footway-bicycle", "footway-sidewalk", "footway-crossing"}),
    "path": frozenset({"path", "path-bicycle", "path-difficult", "path-expert"}),
    "service": frozenset({"service", "service-driveway", "service-parking_aisle"}),
}


def _highway_class_osm(tags: dict) -> str | None:
    hw = tags.get("highway")
    return hw.strip().lower() if hw else None


def _parse_mwm_highway(types: list | None) -> tuple[str | None, bool, bool]:
    """Return (base_class, is_bridge, is_tunnel) from MWM type strings.

    CoMaps encodes bridges/tunnels as ``highway-<class>-bridge`` / ``-tunnel``.
    Compound stems (``service-driveway``, ``footway-bicycle``) are kept intact
    after the bridge/tunnel suffix is stripped.
    """
    raw = None
    for t in types or []:
        if isinstance(t, str) and t.startswith("highway-"):
            raw = t[len("highway-") :].lower()
            break
    if not raw:
        return None, False, False
    is_bridge = raw.endswith("-bridge")
    is_tunnel = raw.endswith("-tunnel")
    if is_bridge:
        base = raw[: -len("-bridge")]
    elif is_tunnel:
        base = raw[: -len("-tunnel")]
    else:
        base = raw
    return base or None, is_bridge, is_tunnel


def _highway_class(tags_or_types) -> str | None:
    """Legacy helper: OSM tags → class; MWM types → base class (suffix stripped)."""
    if isinstance(tags_or_types, dict):
        return _highway_class_osm(tags_or_types)
    base, _, _ = _parse_mwm_highway(tags_or_types)
    return base


def _classes_compatible(osm_hw: str | None, mwm_base: str | None) -> bool:
    if not osm_hw or not mwm_base:
        return True
    if osm_hw == mwm_base:
        return True
    aliases = HIGHWAY_ALIASES.get(osm_hw)
    if aliases and mwm_base in aliases:
        return True
    # OSM highway=service vs MWM highway-service-driveway / -parking_aisle.
    if mwm_base.startswith(osm_hw + "-"):
        return True
    if osm_hw.startswith(mwm_base + "-"):
        return True
    return False


def _name(tags_or_props) -> str:
    if isinstance(tags_or_props, dict):
        for k in ("name", "bridge:name", "ref"):
            v = tags_or_props.get(k)
            if v and str(v).strip():
                return str(v).strip().lower()
    return ""


def _truthy_tag(val) -> bool:
    if val is None:
        return False
    return str(val).strip().lower() not in ("", "no", "false", "0")


def _qpair(lat: float, lon: float) -> tuple[int, int]:
    return quantise_coord(lat), quantise_coord(lon)


def _parse_tag(key: str, raw: str | None, kind: str) -> float | None:
    """Parse one tag; log and skip unparseable non-empty values."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    cls = classify_tag_value(s)
    if cls != "numeric":
        if cls == "unparseable":
            print(
                json.dumps({"skip_unparseable": True, "key": key, "value": s}),
                file=sys.stderr,
                flush=True,
            )
        return None
    if kind == "length":
        return parse_length_metres(s)
    return parse_weight_tonnes(s)


def classify_way_limit_tags(tags: dict) -> str:
    """Way-level class for match-rate denominator.

    Returns one of: ``numeric``, ``non_numeric_no_limit``, ``unknown_value``,
    ``unparseable``. Priority when mixed non-numeric tags: unknown_value >
    unparseable > non_numeric_no_limit. Any numeric limit wins as numeric.
    """
    classes = []
    for key in LIMIT_KEYS:
        raw = tags.get(key)
        if raw is None or not str(raw).strip():
            continue
        classes.append(classify_tag_value(str(raw)))
    if not classes:
        return "unparseable"
    if "numeric" in classes:
        return "numeric"
    if "unknown_value" in classes:
        return "unknown_value"
    if "unparseable" in classes:
        return "unparseable"
    return "non_numeric_no_limit"


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


def _m_per_deg(lat: float) -> tuple[float, float]:
    return 111320.0, 111320.0 * math.cos(math.radians(lat))


def _dist_point_to_segment_m(
    pt: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    lat0 = (pt[0] + a[0] + b[0]) / 3.0
    m_lat, m_lon = _m_per_deg(lat0)
    abx = (b[1] - a[1]) * m_lon
    aby = (b[0] - a[0]) * m_lat
    apx = (pt[1] - a[1]) * m_lon
    apy = (pt[0] - a[0]) * m_lat
    ab2 = abx * abx + aby * aby
    t = 0.0 if ab2 == 0.0 else max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
    return math.hypot(apx - t * abx, apy - t * aby)


def _dist_point_to_polyline_m(
    pt: tuple[float, float],
    poly: list[tuple[float, float]],
) -> float:
    best = 1e18
    for i in range(len(poly) - 1):
        best = min(best, _dist_point_to_segment_m(pt, poly[i], poly[i + 1]))
    return best


def _segment_length_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    m_lat, m_lon = _m_per_deg((a[0] + b[0]) / 2.0)
    return math.hypot((b[1] - a[1]) * m_lon, (b[0] - a[0]) * m_lat)


def _polyline_length_m(nodes: list[tuple[float, float]]) -> float:
    return sum(
        _segment_length_m(nodes[i], nodes[i + 1]) for i in range(len(nodes) - 1)
    )


def _project_along_polyline_m(
    pt: tuple[float, float],
    poly: list[tuple[float, float]],
) -> tuple[float, float]:
    """Return (along_track_m, distance_m) for the closest point on ``poly``."""
    best_d = 1e18
    best_s = 0.0
    acc = 0.0
    for i in range(len(poly) - 1):
        a, b = poly[i], poly[i + 1]
        seg_l = _segment_length_m(a, b)
        m_lat, m_lon = _m_per_deg((pt[0] + a[0] + b[0]) / 3.0)
        abx = (b[1] - a[1]) * m_lon
        aby = (b[0] - a[0]) * m_lat
        apx = (pt[1] - a[1]) * m_lon
        apy = (pt[0] - a[0]) * m_lat
        ab2 = abx * abx + aby * aby
        t = 0.0 if ab2 == 0.0 else max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
        d = math.hypot(apx - t * abx, apy - t * aby)
        if d < best_d:
            best_d = d
            best_s = acc + t * seg_l
        acc += seg_l
    return best_s, best_d


def _along_way_overlap_ratio(
    a: tuple[float, float],
    b: tuple[float, float],
    way_nodes: list[tuple[float, float]],
    way_len_m: float,
) -> tuple[float, float, float]:
    """Return (ratio, along_span_m, segment_length_m).

    ratio = along_span / max(min(seg_len, way_len), OVERLAP_DEG_EPS_M).
    """
    seg_l = _segment_length_m(a, b)
    sa, _ = _project_along_polyline_m(a, way_nodes)
    sb, _ = _project_along_polyline_m(b, way_nodes)
    span = abs(sa - sb)
    denom = max(min(seg_l, way_len_m), OVERLAP_DEG_EPS_M)
    return span / denom, span, seg_l


def _bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    m_lat, m_lon = _m_per_deg((a[0] + b[0]) / 2.0)
    dx = (b[1] - a[1]) * m_lon
    dy = (b[0] - a[0]) * m_lat
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _angle_diff_deg(a: float, b: float) -> float:
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _nearest_way_bearing(
    a: tuple[float, float],
    b: tuple[float, float],
    way_nodes: list[tuple[float, float]],
) -> float | None:
    """Bearing of the OSM way segment closest to the midpoint of (a, b)."""
    if len(way_nodes) < 2:
        return None
    mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    best_i = 0
    best_d = 1e18
    for i in range(len(way_nodes) - 1):
        d = _dist_point_to_segment_m(mid, way_nodes[i], way_nodes[i + 1])
        if d < best_d:
            best_d = d
            best_i = i
    return _bearing_deg(way_nodes[best_i], way_nodes[best_i + 1])


def _overall_way_bearing(way_nodes: list[tuple[float, float]]) -> float | None:
    """Chord bearing from first to last node (helps short winding bridges)."""
    if len(way_nodes) < 2:
        return None
    if _segment_length_m(way_nodes[0], way_nodes[-1]) < OVERLAP_DEG_EPS_M:
        return None
    return _bearing_deg(way_nodes[0], way_nodes[-1])


def _bearing_ok(
    seg_bearing: float,
    way_nodes: list[tuple[float, float]],
    a: tuple[float, float],
    b: tuple[float, float],
) -> bool:
    """Accept if nearest OSM segment OR overall way chord is within tolerance."""
    candidates: list[float] = []
    near = _nearest_way_bearing(a, b, way_nodes)
    if near is not None:
        candidates.append(near)
    overall = _overall_way_bearing(way_nodes)
    if overall is not None and (not candidates or overall != candidates[0]):
        candidates.append(overall)
    for way_bearing in candidates:
        diff = _angle_diff_deg(seg_bearing, way_bearing)
        if min(diff, abs(diff - 180.0)) <= MAX_BEARING_DIFF_DEG:
            return True
    return False


def _parse_way_id(props: dict) -> int | None:
    """Parse osmium ``-u type_id`` / ``-a type,id`` / pyosmium ``way/N`` ids."""
    if props.get("@type") == "way":
        cand = props.get("@id")
        if isinstance(cand, int):
            return cand
        if isinstance(cand, str) and cand.isdigit():
            return int(cand)
    for cand in (props.get("@id"), props.get("id"), props.get("osm_id")):
        if cand is None:
            continue
        s = str(cand)
        if s.startswith("way/"):
            return int(s.split("/", 1)[1])
        if s.startswith("w") and s[1:].isdigit():
            return int(s[1:])
        if s.isdigit():
            return int(s)
    return None


def load_tagged_ways_geojson(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ways = []
    for feat in data.get("features", []):
        props = feat.get("properties") or {}
        keep = set(LIMIT_KEYS) | {
            "highway",
            "name",
            "bridge:name",
            "ref",
            "bridge",
            "tunnel",
            "layer",
            "@id",
            "id",
            "osm_id",
            "@type",
        }
        tags = {k: v for k, v in props.items() if k in keep}
        if "highway" not in tags and props.get("highway"):
            tags["highway"] = props["highway"]
        geom = feat.get("geometry") or {}
        if geom.get("type") != "LineString":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        nodes = [(float(c[1]), float(c[0])) for c in coords]  # lat, lon
        ways.append(
            {
                "osm_way_id": _parse_way_id(props),
                "tags": tags,
                "nodes": nodes,
                "name": _name(tags),
                "highway": _highway_class_osm(tags),
                "is_bridge": _truthy_tag(tags.get("bridge")),
                "is_tunnel": _truthy_tag(tags.get("tunnel")),
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
            props = {
                k: tags[k]
                for k in tags
                if k in limit_keys
                or k in ("highway", "name", "bridge:name", "ref", "bridge", "tunnel", "layer")
            }
            props["@id"] = f"way/{w.id}"
            props["@type"] = "way"
            self.features.append(
                {
                    "type": "Feature",
                    "properties": props,
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
            )

    h = Handler()
    h.apply_file(str(pbf), locations=True, idx="flex_mem")
    out_geojson.write_text(
        json.dumps({"type": "FeatureCollection", "features": h.features}, ensure_ascii=False),
        encoding="utf-8",
    )


def filter_pbf_to_geojson(
    pbf: Path,
    out_geojson: Path,
    bbox: tuple[float, float, float, float] | None,
    poly: Path | None = None,
) -> None:
    """Stream-filter a PBF to tagged highway ways, then export GeoJSON."""
    if not _have_osmium_cli():
        filter_pbf_to_geojson_pyosmium(pbf, out_geojson, bbox)
        return
    with tempfile.TemporaryDirectory(prefix="navi-vl-") as td:
        td_path = Path(td)
        work = pbf
        clipped = td_path / "clip.pbf"
        if poly is not None and poly.is_file():
            subprocess.check_call(
                [
                    "osmium",
                    "extract",
                    f"--polygon={poly}",
                    "-o",
                    str(clipped),
                    "--overwrite",
                    str(pbf),
                ]
            )
            work = clipped
        elif bbox is not None:
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
        # Preserve OSM way ids (type_id) and @type/@id attributes.
        subprocess.check_call(
            [
                "osmium",
                "export",
                "-f",
                "geojson",
                "-u",
                "type_id",
                "-a",
                "type,id",
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
            types = feat.get("types") or []
            base, is_bridge, is_tunnel = _parse_mwm_highway(types)
            roads.append(
                {
                    "feature_id": int(feat["feature_id"]),
                    "points": [(float(p["lat"]), float(p["lon"])) for p in pts],
                    "highway": base,
                    "is_bridge": is_bridge,
                    "is_tunnel": is_tunnel,
                    "name": _name(feat.get("tags") or feat),
                    "types": types,
                }
            )
    return roads


def _build_road_grid(roads: list[dict]) -> dict[tuple[int, int], list[int]]:
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    cell = GRID_CELL_QUANTA
    for ri, road in enumerate(roads):
        keys: set[tuple[int, int]] = set()
        for lat, lon in road["points"]:
            keys.add((quantise_coord(lat) // cell, quantise_coord(lon) // cell))
        for key in keys:
            grid[key].append(ri)
    return grid


def _candidate_road_indices(
    way: dict,
    grid: dict[tuple[int, int], list[int]],
    pad: int = 2,
) -> list[int]:
    cell = GRID_CELL_QUANTA
    seen: set[int] = set()
    out: list[int] = []
    for lat, lon in way["nodes"]:
        qlat = quantise_coord(lat) // cell
        qlon = quantise_coord(lon) // cell
        for di in range(-pad, pad + 1):
            for dj in range(-pad, pad + 1):
                for ri in grid.get((qlat + di, qlon + dj), ()):
                    if ri not in seen:
                        seen.add(ri)
                        out.append(ri)
    return out


def match_way_to_roads(
    way: dict,
    roads: list[dict],
    *,
    grid: dict[tuple[int, int], list[int]] | None = None,
    tolerance_m: float = TOLERANCE_M,
    guard_counts: dict[str, int] | None = None,
) -> list[dict]:
    """Return vehicle_limits JSONL rows for one OSM way, or empty if unmatched.

    When ``grid`` is provided, only spatially nearby roads are considered.
    ``guard_counts`` accumulates false-positive guard rejections when given.
    """
    lim = limits_from_tags(way["tags"])
    if lim is None:
        return []
    hw = way["highway"]
    wname = way["name"]
    way_nodes = way["nodes"]
    osm_bridge = bool(way.get("is_bridge"))
    osm_tunnel = bool(way.get("is_tunnel"))

    if grid is None:
        indices = list(range(len(roads)))
    else:
        indices = _candidate_road_indices(way, grid)

    # Collect candidate (feature_id, seg_i, score, row, seg_len). Lower score
    # is better: score = (name_mismatch, mean_endpoint_dist).
    candidates: dict[tuple[int, int], tuple[tuple, dict, float]] = {}
    way_len_m = _polyline_length_m(way_nodes)

    for ri in indices:
        road = roads[ri]
        if not _classes_compatible(hw, road["highway"]):
            if guard_counts is not None:
                guard_counts["class_incompatible"] = guard_counts.get("class_incompatible", 0) + 1
            continue
        # Bridge/tunnel layer: road under a bridge must not inherit the bridge limit.
        if osm_bridge and not road["is_bridge"]:
            if guard_counts is not None:
                guard_counts["bridge_layer_reject"] = guard_counts.get("bridge_layer_reject", 0) + 1
            continue
        if osm_tunnel and not road["is_tunnel"]:
            if guard_counts is not None:
                guard_counts["tunnel_layer_reject"] = guard_counts.get("tunnel_layer_reject", 0) + 1
            continue
        if (not osm_bridge) and (not osm_tunnel) and road["is_bridge"]:
            if guard_counts is not None:
                guard_counts["nonbridge_to_bridge_reject"] = (
                    guard_counts.get("nonbridge_to_bridge_reject", 0) + 1
                )
            continue
        if (not osm_bridge) and (not osm_tunnel) and road["is_tunnel"]:
            if guard_counts is not None:
                guard_counts["nontunnel_to_tunnel_reject"] = (
                    guard_counts.get("nontunnel_to_tunnel_reject", 0) + 1
                )
            continue

        rname = road["name"]
        name_mismatch = 1 if (wname and rname and wname != rname) else 0
        pts = road["points"]
        for seg_i in range(len(pts) - 1):
            a = pts[seg_i]
            b = pts[seg_i + 1]
            da = _dist_point_to_polyline_m(a, way_nodes)
            db = _dist_point_to_polyline_m(b, way_nodes)
            if da > tolerance_m or db > tolerance_m:
                continue
            seg_len_m = _segment_length_m(a, b)
            # Along-way overlap first so bridge same-layer can skip bearing.
            overlap_ok = True
            if seg_len_m >= OVERLAP_DEG_EPS_M:
                ratio, _span, _ = _along_way_overlap_ratio(
                    a, b, way_nodes, way_len_m
                )
                min_ratio = OVERLAP_MIN_RATIO
                if way_len_m <= SHORT_WAY_OVERLAP_M:
                    min_ratio = OVERLAP_MIN_RATIO_SHORT_WAY
                if ratio < min_ratio:
                    overlap_ok = False
                    if guard_counts is not None:
                        guard_counts["overlap_reject"] = (
                            guard_counts.get("overlap_reject", 0) + 1
                        )
                    continue
            # Same-layer bridge (or tunnel): both ends already within tolerance
            # and overlap passed → skip bearing (short bridge chords wind).
            skip_bearing = (
                overlap_ok
                and da <= tolerance_m
                and db <= tolerance_m
                and (
                    (osm_bridge and road["is_bridge"])
                    or (osm_tunnel and road["is_tunnel"])
                )
            )
            if skip_bearing:
                if guard_counts is not None:
                    guard_counts["bearing_skipped_same_layer"] = (
                        guard_counts.get("bearing_skipped_same_layer", 0) + 1
                    )
            elif seg_len_m >= MIN_BEARING_SEGMENT_M:
                # Degenerate CoMaps stubs have meaningless bearings — handled
                # in the else branch below.
                seg_bearing = _bearing_deg(a, b)
                if not _bearing_ok(seg_bearing, way_nodes, a, b):
                    if guard_counts is not None:
                        guard_counts["bearing_reject"] = (
                            guard_counts.get("bearing_reject", 0) + 1
                        )
                    continue
            else:
                if guard_counts is not None:
                    guard_counts["bearing_skipped_short_seg"] = (
                        guard_counts.get("bearing_skipped_short_seg", 0) + 1
                    )
            # Prefer name match; then closer mean endpoint distance.
            score = (name_mismatch, (da + db) / 2.0)
            row = {
                "feature_id": road["feature_id"],
                "segment_index": seg_i,
                **lim,
                "osm_way_id": way["osm_way_id"],
            }
            key = (road["feature_id"], seg_i)
            prev = candidates.get(key)
            if prev is None or score < prev[0]:
                candidates[key] = (score, row, seg_len_m)

    if not candidates:
        return []

    # Primary feature = most non-degenerate segments (then most segments).
    # Short segments on a *different* feature are junction stubs leaving the
    # tagged way (bearing was skipped for them).
    by_fid: dict[int, list[tuple[tuple, dict, float]]] = defaultdict(list)
    for item in candidates.values():
        by_fid[item[1]["feature_id"]].append(item)

    def _fid_score(fid: int) -> tuple[int, int, float]:
        items = by_fid[fid]
        n_long = sum(1 for _, _, L in items if L >= OVERLAP_DEG_EPS_M)
        return (n_long, len(items), sum(L for _, _, L in items))

    primary_fid = max(by_fid.keys(), key=_fid_score)
    kept: list[dict] = []
    for _score, row, seg_len_m in candidates.values():
        if row["feature_id"] != primary_fid and seg_len_m < MIN_BEARING_SEGMENT_M:
            if guard_counts is not None:
                guard_counts["junction_stub_reject"] = (
                    guard_counts.get("junction_stub_reject", 0) + 1
                )
            continue
        kept.append(row)

    kept.sort(key=lambda r: (r["feature_id"], r["segment_index"]))
    return kept


def annotate(
    ways: list[dict],
    roads: list[dict],
    *,
    tolerance_m: float = TOLERANCE_M,
) -> tuple[list[dict], dict]:
    records: list[dict] = []
    matched_ways = 0
    non_numeric_no_limit = 0
    unknown_value = 0
    skipped_unparseable = 0
    unparseable: list[dict] = []
    unknown_value_ways: list[dict] = []
    unmatched: list[dict] = []
    matched_segments_per_way: list[dict] = []
    guard_counts: dict[str, int] = defaultdict(int)
    grid = _build_road_grid(roads)

    for way in ways:
        raw_tags = {k: way["tags"].get(k) for k in LIMIT_KEYS if way["tags"].get(k)}
        way_cls = classify_way_limit_tags(way["tags"])
        if way_cls != "numeric":
            entry = {
                "osm_way_id": way["osm_way_id"],
                "name": way["name"],
                "highway": way["highway"],
                "tags": raw_tags,
                "class": way_cls,
            }
            if way_cls == "non_numeric_no_limit":
                non_numeric_no_limit += 1
            elif way_cls == "unknown_value":
                unknown_value += 1
                unknown_value_ways.append(entry)
            else:
                skipped_unparseable += 1
                unparseable.append(entry)
                print(
                    json.dumps({"vehicle_limits_unparseable": entry}, ensure_ascii=False),
                    file=sys.stderr,
                    flush=True,
                )
            continue
        # Numeric way — limits_from_tags must succeed.
        if limits_from_tags(way["tags"]) is None:
            skipped_unparseable += 1
            entry = {
                "osm_way_id": way["osm_way_id"],
                "name": way["name"],
                "highway": way["highway"],
                "tags": raw_tags,
                "class": "unparseable",
            }
            unparseable.append(entry)
            continue
        rows = match_way_to_roads(
            way,
            roads,
            grid=grid,
            tolerance_m=tolerance_m,
            guard_counts=guard_counts,
        )
        if not rows:
            # Diagnose nearest for the unmatched list reason.
            reason = "no_nearby_road"
            cands = _candidate_road_indices(way, grid)
            if cands:
                reason = "no_segment_within_tolerance_or_guards"
            unmatched.append(
                {
                    "osm_way_id": way["osm_way_id"],
                    "name": way["name"],
                    "highway": way["highway"],
                    "tags": raw_tags,
                    "reason": reason,
                    "is_bridge": bool(way.get("is_bridge")),
                    "is_tunnel": bool(way.get("is_tunnel")),
                }
            )
            continue
        matched_ways += 1
        matched_segments_per_way.append(
            {
                "osm_way_id": way["osm_way_id"],
                "name": way["name"],
                "segments": len(rows),
            }
        )
        records.extend(rows)

    # Dedup by (feature_id, segment_index); prefer name-matched / closer via
    # last-writer only when scores equal — re-score by keeping first seen from
    # matched_ways order is weak; re-run: last wins is OK when limits agree.
    by_key: dict[tuple[int, int], dict] = {}
    for r in records:
        by_key[(int(r["feature_id"]), int(r["segment_index"]))] = r
    deduped = list(by_key.values())
    deduped.sort(key=lambda r: (r["feature_id"], r["segment_index"]))
    excluded = non_numeric_no_limit + unknown_value + skipped_unparseable
    numeric_ways = max(0, len(ways) - excluded)
    # Backward-compat: skipped_unparseable = all non-numeric exclusions.
    skipped_total = excluded
    report = {
        "tagged_ways": len(ways),
        "non_numeric_no_limit": non_numeric_no_limit,
        "unknown_value": unknown_value,
        "skipped_unparseable": skipped_total,
        "skipped_unparseable_other": skipped_unparseable,
        "numeric_ways": numeric_ways,
        "matched_ways": matched_ways,
        "unmatched_ways": len(unmatched),
        "match_rate": (matched_ways / numeric_ways) if numeric_ways else 0.0,
        "records": len(deduped),
        "tolerance_m": tolerance_m,
        "max_bearing_diff_deg": MAX_BEARING_DIFF_DEG,
        "guard_rejections": dict(guard_counts),
        "matched_segments_per_way": matched_segments_per_way,
        "unparseable": unparseable,
        "unknown_value_ways": unknown_value_ways,
        "unmatched": unmatched,
        "osm_data_date": "",
        "map_release": "",
    }
    return deduped, report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--roads-jsonl", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument(
        "--tolerance-m",
        type=float,
        default=TOLERANCE_M,
        help=f"both-endpoint distance to OSM polyline (default {TOLERANCE_M})",
    )
    ap.add_argument(
        "--osm-data-date",
        type=str,
        default=os.environ.get("NAVI_MWM_OSM_DATA_DATE", ""),
        help="OSM extract date (YYYY-MM-DD) recorded in coverage",
    )
    ap.add_argument(
        "--map-release",
        type=str,
        default=os.environ.get("NAVI_MWM_COMAPS_VERSION", ""),
        help="CoMaps map release id recorded in coverage (e.g. 261105)",
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--geojson", type=Path, help="Pre-exported tagged ways GeoJSON")
    src.add_argument("--pbf", type=Path, help="Geofabrik extract; filtered then deleted")
    ap.add_argument(
        "--bbox",
        type=str,
        default=None,
        help="west,south,east,north for --pbf clip (leaf border bbox)",
    )
    ap.add_argument(
        "--poly",
        type=Path,
        default=None,
        help="leaf .poly for --pbf clip (preferred over --bbox)",
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
            filter_pbf_to_geojson(args.pbf, gj, bbox, poly=args.poly)
            ways = load_tagged_ways_geojson(gj)
        finally:
            gj.unlink(missing_ok=True)

    roads = load_roads_jsonl(args.roads_jsonl)
    records, report = annotate(ways, roads, tolerance_m=args.tolerance_m)
    report["osm_data_date"] = (args.osm_data_date or "").strip()
    report["map_release"] = (args.map_release or "").strip()
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
        if k not in ("unmatched", "unparseable", "matched_segments_per_way")
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
