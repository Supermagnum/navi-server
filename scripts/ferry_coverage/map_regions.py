#!/usr/bin/env python3
"""Step 3: assign Overpass ferries to published / conf regions via .poly / Geofabrik."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_SCRIPTS = _SCRIPT_DIR.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# Reuse live dem_poly_filter if readable; else vendor minimal copy via import path
_LIVE_LIB = Path("/media/navi/navi-server/scripts/lib")
if _LIVE_LIB.is_dir() and str(_LIVE_LIB) not in sys.path:
    sys.path.insert(0, str(_LIVE_LIB.parent))

from ferry_coverage.common import (  # noqa: E402
    COMPOSITE_BAKE_IDS,
    FerryRecord,
    is_composite_bake_id,
    iter_ferries_from_overpass,
    load_json,
    write_json,
)

try:
    from lib.dem_poly_filter import load_poly_file, point_in_poly  # type: ignore
except Exception:  # noqa: BLE001
    from ferry_coverage.dem_poly_filter_fallback import (  # type: ignore
        load_poly_file,
        point_in_poly,
    )


GEOFABRIK_INDEX = "https://download.geofabrik.de/index-v1.json"
USER_AGENT = "navi-ferry-coverage/1.0 (+https://github.com/Supermagnum/navi-server)"


def region_id_from_path(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")


def parse_regions_conf(path: Path) -> dict[str, str]:
    """bake_id -> source line."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) < 2:
            continue
        # tab or whitespace: first token id, second source
        if "\t" in s:
            rid, rest = s.split("\t", 1)
            src = rest.split()[0]
        else:
            rid, src = parts[0], parts[1]
        out[rid.strip()] = src.strip()
    return out


def geofabrik_path_from_source(src: str) -> str | None:
    if src.startswith("geofabrik:"):
        return src.split(":", 1)[1]
    return None


def load_geofabrik_index(cache: Path) -> dict[str, Any]:
    if cache.is_file():
        return load_json(cache)
    req = urllib.request.Request(GEOFABRIK_INDEX, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(raw)
    return json.loads(raw.decode("utf-8"))


def bbox_from_geometry(geom: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    """Return (min_lat, min_lon, max_lat, max_lon) from GeoJSON geometry."""
    if not geom:
        return None
    coords: list[tuple[float, float]] = []

    def walk(c: Any) -> None:
        if isinstance(c, (list, tuple)):
            if c and isinstance(c[0], (int, float)) and len(c) >= 2:
                lon, lat = float(c[0]), float(c[1])
                coords.append((lat, lon))
            else:
                for x in c:
                    walk(x)

    walk(geom.get("coordinates"))
    if not coords:
        return None
    lats = [a for a, _ in coords]
    lons = [b for _, b in coords]
    return min(lats), min(lons), max(lats), max(lons)


def point_in_bbox(lat: float, lon: float, bb: tuple[float, float, float, float]) -> bool:
    min_lat, min_lon, max_lat, max_lon = bb
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


class RegionShape:
    def __init__(
        self,
        bake_id: str,
        region_id: str | None,
        rings: Any | None,
        bbox: tuple[float, float, float, float] | None,
        source: str,
        composite: bool,
        in_weekly: bool,
        in_planet: bool,
        in_current: bool,
    ) -> None:
        self.bake_id = bake_id
        self.region_id = region_id
        self.rings = rings
        self.bbox = bbox
        self.source = source
        self.composite = composite
        self.in_weekly = in_weekly
        self.in_planet = in_planet
        self.in_current = in_current
        if self.rings is not None and self.bbox is None:
            lons = [p[0] for ring in self.rings for p in ring]
            lats = [p[1] for ring in self.rings for p in ring]
            if lats and lons:
                self.bbox = (min(lats), min(lons), max(lats), max(lons))

    def contains(self, lat: float, lon: float) -> bool:
        if self.bbox is not None and not point_in_bbox(lat, lon, self.bbox):
            return False
        if self.rings is not None:
            return point_in_poly(lon, lat, self.rings)
        return self.bbox is not None


def try_load_poly(extracts: Path, bake_id: str) -> Any | None:
    p = extracts / f"{bake_id}.poly"
    if p.is_file():
        try:
            return load_poly_file(p)
        except Exception as e:  # noqa: BLE001
            print(f"WARN poly parse {p}: {e}", flush=True)
    return None


def fetch_osmfr_poly(scratch: Path, relative: str) -> Any | None:
    """relative like europe/norway/hedmark or europe/sweden/dalarna."""
    cache = scratch / "regions" / "polys" / f"{region_id_from_path(relative)}.poly"
    if not cache.is_file():
        url = f"https://download.openstreetmap.fr/polygons/{relative}.poly"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(resp.read())
        except Exception as e:  # noqa: BLE001
            print(f"WARN osm.fr poly {url}: {e}", flush=True)
            return None
    try:
        return load_poly_file(cache)
    except Exception as e:  # noqa: BLE001
        print(f"WARN osm.fr poly parse {cache}: {e}", flush=True)
        return None


def build_shapes(
    scratch: Path,
    live_data: Path,
) -> list[RegionShape]:
    extracts = live_data / "scratch" / "extracts"
    weekly = parse_regions_conf(live_data / "regions.conf")
    planet = parse_regions_conf(live_data / "regions.planet.conf")
    current = load_json(scratch / "current.json")
    current_by_bake = {r["bake_id"]: r for r in current.get("regions") or []}

    idx = load_geofabrik_index(scratch / "regions" / "index-v1.json")
    gf_by_path: dict[str, dict[str, Any]] = {}
    for feat in idx.get("features") or []:
        props = feat.get("properties") or {}
        pid = props.get("id")
        if pid:
            gf_by_path[pid] = {"props": props, "geometry": feat.get("geometry")}

    # Children map for composite detection
    children: dict[str, list[str]] = defaultdict(list)
    for pid, ent in gf_by_path.items():
        parent = (ent["props"] or {}).get("parent")
        if parent:
            children[parent].append(pid)

    bake_ids = set(weekly) | set(planet) | set(current_by_bake)
    shapes: list[RegionShape] = []

    for bake_id in sorted(bake_ids):
        src = weekly.get(bake_id) or planet.get(bake_id) or ""
        region_id = None
        if bake_id in current_by_bake:
            region_id = current_by_bake[bake_id].get("region_id")

        gf_path = geofabrik_path_from_source(src)
        if not gf_path and region_id:
            gf_path = region_id

        rings = try_load_poly(extracts, bake_id)
        bbox = None
        shape_src = "missing"

        # OSM.fr specials
        if bake_id == "hedmark":
            rings = rings or fetch_osmfr_poly(scratch, "europe/norway/hedmark")
            shape_src = "osm.fr.poly" if rings else shape_src
        elif bake_id == "europe_sweden_dalarna":
            rings = rings or fetch_osmfr_poly(scratch, "europe/sweden/dalarna")
            shape_src = "osm.fr.poly" if rings else shape_src
        elif src.startswith("url:") and "openstreetmap.fr" in src:
            # try derive path from URL
            m = re.search(r"/extracts/(.+)-latest\.osm\.pbf", src)
            if m:
                rings = rings or fetch_osmfr_poly(scratch, m.group(1))
                shape_src = "osm.fr.poly" if rings else shape_src

        if rings is not None and shape_src == "missing":
            shape_src = "extract.poly"

        if rings is None and gf_path and gf_path in gf_by_path:
            geom = gf_by_path[gf_path]["geometry"]
            bbox = bbox_from_geometry(geom)
            shape_src = "geofabrik.index-v1.bbox"
            # Prefer bboxes.json if present (tighter)
        # conf bboxes
        for bbox_file, label in (
            (live_data / "regions.conf.bboxes.json", "regions.conf.bboxes"),
            (live_data / "regions.planet.conf.bboxes.json", "regions.planet.conf.bboxes"),
        ):
            if rings is not None:
                break
            if not bbox_file.is_file():
                continue
            bbmap = load_json(bbox_file)
            if bake_id in bbmap:
                arr = bbmap[bake_id]
                if isinstance(arr, list) and len(arr) == 4:
                    bbox = (float(arr[0]), float(arr[1]), float(arr[2]), float(arr[3]))
                    shape_src = label

        composite = is_composite_bake_id(bake_id) or (
            bool(gf_path and children.get(gf_path))
        )
        if bake_id in COMPOSITE_BAKE_IDS:
            composite = True

        shapes.append(
            RegionShape(
                bake_id=bake_id,
                region_id=region_id,
                rings=rings,
                bbox=bbox,
                source=shape_src,
                composite=composite,
                in_weekly=bake_id in weekly,
                in_planet=bake_id in planet,
                in_current=bake_id in current_by_bake,
            )
        )

    # Prefer leaf shapes: when testing containment we still assign to ALL containing
    # regions (user asked: assign to every region containing endpoint or geometry).
    return shapes


def assign_ferries(records: list[FerryRecord], shapes: list[RegionShape]) -> None:
    # bbox prefilter acceleration
    for rec in records:
        hits: list[str] = []
        pts = rec.points
        if not pts:
            rec.regions = []
            rec.cross_region = False
            continue
        # sample: endpoints + up to 8 midpoints
        sample = [pts[0], pts[-1]]
        if len(pts) > 2:
            step = max(1, len(pts) // 8)
            sample.extend(pts[i] for i in range(0, len(pts), step))
        # dedupe
        seen_pts = list(dict.fromkeys(sample))
        for sh in shapes:
            if sh.rings is None and sh.bbox is None:
                continue
            # quick bbox reject when we have rings: compute ring bbox lazily? skip for now
            if sh.bbox is not None and sh.rings is None:
                if not any(point_in_bbox(lat, lon, sh.bbox) for lat, lon in seen_pts):
                    continue
                hits.append(sh.bake_id)
                continue
            if any(sh.contains(lat, lon) for lat, lon in seen_pts):
                hits.append(sh.bake_id)
        rec.regions = sorted(set(hits))
        # cross-region: endpoints in different regions (leaf-ish: non-composite)
        leaf_hits_a: set[str] = set()
        leaf_hits_b: set[str] = set()
        a, b = pts[0], pts[-1]
        for sh in shapes:
            if sh.composite:
                continue
            if sh.rings is None and sh.bbox is None:
                continue
            if sh.contains(a[0], a[1]):
                leaf_hits_a.add(sh.bake_id)
            if sh.contains(b[0], b[1]):
                leaf_hits_b.add(sh.bake_id)
        rec.cross_region = bool(leaf_hits_a and leaf_hits_b and leaf_hits_a != leaf_hits_b)


def run(scratch: Path, live_data: Path) -> dict:
    shapes = build_shapes(scratch, live_data)
    write_json(
        scratch / "out" / "region_shapes_meta.json",
        [
            {
                "bake_id": s.bake_id,
                "region_id": s.region_id,
                "shape_source": s.source,
                "has_poly": s.rings is not None,
                "has_bbox": s.bbox is not None,
                "composite": s.composite,
                "in_weekly": s.in_weekly,
                "in_planet": s.in_planet,
                "in_current": s.in_current,
            }
            for s in shapes
        ],
    )

    records = list(
        iter_ferries_from_overpass(scratch / "overpass" / "route_ferry.json", "route_ferry")
    )
    print(f"assigning {len(records)} route=ferry objects to {len(shapes)} regions...", flush=True)
    assign_ferries(records, shapes)

    # per-region aggregates (route=ferry only)
    per: dict[str, dict[str, Any]] = {}
    for sh in shapes:
        per[sh.bake_id] = {
            "bake_id": sh.bake_id,
            "region_id": sh.region_id,
            "composite": sh.composite,
            "in_weekly": sh.in_weekly,
            "in_planet": sh.in_planet,
            "in_current": sh.in_current,
            "shape_source": sh.source,
            "route_ferry": 0,
            "car_capable": 0,
            "passenger_bicycle_only": 0,
            "unknown": 0,
            "cross_region": 0,
        }

    no_region: list[FerryRecord] = []
    for rec in records:
        if not rec.regions:
            no_region.append(rec)
            continue
        for bid in rec.regions:
            bucket = per.get(bid)
            if not bucket:
                continue
            bucket["route_ferry"] += 1
            if rec.classification == "car-capable":
                bucket["car_capable"] += 1
            elif rec.classification == "passenger-bicycle-only":
                bucket["passenger_bicycle_only"] += 1
            else:
                bucket["unknown"] += 1
            if rec.cross_region:
                bucket["cross_region"] += 1

    # CSV with region assignment
    csv_path = scratch / "out" / "ferries_by_region.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "osm_type",
                "osm_id",
                "classification",
                "name",
                "cross_region",
                "regions",
                "motor_vehicle",
                "motorcar",
                "duration",
            ]
        )
        for rec in records:
            w.writerow(
                [
                    rec.osm_type,
                    rec.osm_id,
                    rec.classification,
                    rec.name,
                    int(rec.cross_region),
                    ";".join(rec.regions),
                    rec.tags.get("motor_vehicle", ""),
                    rec.tags.get("motorcar", ""),
                    rec.tags.get("duration", ""),
                ]
            )

    # also dump assignment for pack compare
    assign_path = scratch / "out" / "ferry_region_assignment.json"
    write_json(
        assign_path,
        [
            {
                "key": rec.key,
                "osm_type": rec.osm_type,
                "osm_id": rec.osm_id,
                "classification": rec.classification,
                "name": rec.name,
                "tags": rec.tags,
                "regions": rec.regions,
                "cross_region": rec.cross_region,
                "endpoints": [rec.points[0], rec.points[-1]] if rec.points else [],
            }
            for rec in records
        ],
    )

    no_region_path = scratch / "out" / "ferries_no_region.json"
    write_json(
        no_region_path,
        [
            {
                "key": r.key,
                "name": r.name,
                "classification": r.classification,
                "tags": r.tags,
                "n_pts": len(r.points),
            }
            for r in no_region
        ],
    )

    summary = {
        "n_shapes": len(shapes),
        "n_ferries": len(records),
        "n_no_region": len(no_region),
        "shapes_with_poly": sum(1 for s in shapes if s.rings is not None),
        "shapes_bbox_only": sum(1 for s in shapes if s.rings is None and s.bbox is not None),
        "shapes_missing": sum(1 for s in shapes if s.rings is None and s.bbox is None),
        "csv_path": str(csv_path),
        "assignment_path": str(assign_path),
        "per_region_path": str(scratch / "out" / "per_region.json"),
    }
    write_json(scratch / "out" / "per_region.json", list(per.values()))
    write_json(scratch / "out" / "map_regions_summary.json", summary)
    print(json.dumps(summary, indent=2))
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--live-data", type=Path, default=Path("/media/navi/navi-server/data"))
    args = ap.parse_args()
    run(args.scratch, args.live_data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
