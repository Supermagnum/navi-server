"""Shared helpers for ferry coverage analysis."""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

ACCESS_YES = {"yes", "true", "1", "designated", "permissive", "official"}
ACCESS_NO = {"no", "false", "0", "private"}

DURATION_RE = re.compile(
    r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$"
)

INTEREST_KEYS = (
    "motor_vehicle",
    "motorcar",
    "vehicle",
    "hgv",
    "access",
    "foot",
    "bicycle",
    "ferry",
    "duration",
    "interval",
    "opening_hours",
    "seasonal",
    "toll",
    "fee",
    "maxspeed",
    "operator",
    "name",
)


def is_access_yes(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in ACCESS_YES


def is_access_no(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in ACCESS_NO


def tags_forbid_mode(tags: dict[str, str], mode: str) -> bool:
    """Mirror pack-convert-core access::tags_forbid_mode for foot/bicycle."""
    specific = tags.get(mode)
    access = tags.get("access")
    if specific is not None:
        if is_access_no(specific):
            return True
        if is_access_yes(specific):
            return False
        return True
    return is_access_no(access)


def ferry_allowed_for_car(tags: dict[str, str]) -> bool:
    """Mirror ferry_allowed_for_profile for Car/Truck."""
    return is_access_yes(tags.get("motor_vehicle")) or is_access_yes(tags.get("motorcar"))


def ferry_allowed_for_foot(tags: dict[str, str]) -> bool:
    return not tags_forbid_mode(tags, "foot")


def ferry_allowed_for_bicycle(tags: dict[str, str]) -> bool:
    return not tags_forbid_mode(tags, "bicycle")


def classify_ferry(tags: dict[str, str]) -> str:
    """car-capable / passenger-bicycle-only / unknown."""
    car = ferry_allowed_for_car(tags)
    foot = ferry_allowed_for_foot(tags)
    bike = ferry_allowed_for_bicycle(tags)
    if car:
        return "car-capable"
    if foot or bike:
        return "passenger-bicycle-only"
    return "unknown"


def parseable_duration(raw: str | None) -> bool:
    if not raw:
        return False
    return bool(DURATION_RE.match(raw))


def tags_indicate_ferry(tags: dict[str, str]) -> bool:
    route = tags.get("route")
    if route and route.lower() == "ferry":
        return True
    if tags.get("highway") == "ferry":
        return True
    ferry = tags.get("ferry")
    return ferry is not None and ferry != "no"


def element_geometry_points(el: dict[str, Any]) -> list[tuple[float, float]]:
    """Return (lat, lon) samples from Overpass geom output."""
    pts: list[tuple[float, float]] = []
    if "geometry" in el and isinstance(el["geometry"], list):
        for g in el["geometry"]:
            if isinstance(g, dict) and "lat" in g and "lon" in g:
                pts.append((float(g["lat"]), float(g["lon"])))
    # relation members may each have geometry
    for mem in el.get("members") or []:
        if not isinstance(mem, dict):
            continue
        for g in mem.get("geometry") or []:
            if isinstance(g, dict) and "lat" in g and "lon" in g:
                pts.append((float(g["lat"]), float(g["lon"])))
        if "lat" in mem and "lon" in mem:
            pts.append((float(mem["lat"]), float(mem["lon"])))
    if "lat" in el and "lon" in el:
        pts.append((float(el["lat"]), float(el["lon"])))
    if "center" in el and isinstance(el["center"], dict):
        c = el["center"]
        if "lat" in c and "lon" in c:
            pts.append((float(c["lat"]), float(c["lon"])))
    return pts


def endpoints(el: dict[str, Any]) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    pts = element_geometry_points(el)
    if not pts:
        return None, None
    if len(pts) == 1:
        return pts[0], pts[0]
    return pts[0], pts[-1]


def osm_key(el: dict[str, Any]) -> str:
    return f"{el.get('type')}/{el.get('id')}"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


COMPOSITE_BAKE_IDS = {
    "europe_dach",
    "europe_alps",
    "europe_britain_and_ireland",
    "europe_great_britain",
    "europe_ireland_and_northern_ireland",
    "asia_sea",
}


def is_composite_bake_id(bake_id: str) -> bool:
    if bake_id in COMPOSITE_BAKE_IDS:
        return True
    # continent roots often composite when children exist; flag known parents
    if bake_id in {
        "africa",
        "antarctica",
        "asia",
        "australia_oceania",
        "central_america",
        "europe",
        "north_america",
        "south_america",
        "russia",
    }:
        return True
    return False


@dataclass
class FerryRecord:
    osm_type: str
    osm_id: int
    tags: dict[str, str]
    classification: str
    points: list[tuple[float, float]] = field(default_factory=list)
    source: str = "route_ferry"  # or ferry_tag_only
    regions: list[str] = field(default_factory=list)
    cross_region: bool = False

    @property
    def key(self) -> str:
        return f"{self.osm_type}/{self.osm_id}"

    @property
    def name(self) -> str:
        return self.tags.get("name") or self.tags.get("name:en") or ""


def normalize_tags(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def iter_ferries_from_overpass(path: Path, source: str) -> Iterable[FerryRecord]:
    data = load_json(path)
    for el in data.get("elements") or []:
        tags = normalize_tags(el.get("tags"))
        if source == "route_ferry":
            # keep route=ferry ways/relations
            if not (tags.get("route") or "").lower() == "ferry":
                # still keep if type route relation without duplicated tag? Overpass filtered
                pass
        pts = element_geometry_points(el)
        yield FerryRecord(
            osm_type=str(el.get("type")),
            osm_id=int(el.get("id")),
            tags=tags,
            classification=classify_ferry(tags),
            points=pts,
            source=source,
        )
