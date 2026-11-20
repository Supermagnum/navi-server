"""Navi .rkyv region ↔ CoMaps leaf polygon overlap + country-grouped leaf list.

Read-only against Geofabrik/Osmosis .poly extracts and CoMaps data/borders.
Area ratios use a local equirectangular (cos φ) scale — relative shares are
stable enough for the 0.5% include threshold without pyproj.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from shapely import STRtree, affinity
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid

# Reuse Osmosis .poly parser from the DEM helper (same Geofabrik format).
import sys

_SCRIPTS_LIB = Path(__file__).resolve().parents[2] / "lib"
if str(_SCRIPTS_LIB) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_LIB))
from dem_poly_filter import parse_osmosis_poly  # noqa: E402

# Default: leaf covers ≥ this share of the Navi region → include.
DEFAULT_INCLUDE_THRESHOLD_PCT = 0.5
# Secondary include: tiny land-border overlap (CoMaps borders are land-only;
# sub-0.5% overlaps still carry border roads/places). Proxy for "any overlap
# containing roads/places" until per-edge OSM sampling exists.
DEFAULT_MICRO_THRESHOLD_PCT = 0.05
# Reject leaves that lie mostly outside the Navi region unless they still
# cover a substantial share of it. Without this, sea/fjord in Geofabrik polys
# + land-only CoMaps borders pull in neighbouring-country leaves (e.g. DK/SE
# under europe/norway/ostlandet) that barely touch the region.
DEFAULT_MOSTLY_OUTSIDE_MIN_COVERAGE_PCT = 25.0
DEFAULT_MOSTLY_OUTSIDE_OVERSPILL_PCT = 50.0
DEFAULT_GAP_WARN_PCT = 0.5
DEFAULT_MAX_LEAVES_WARN = 8
DEFAULT_SIMPLIFY_DEG = 0.002
NON_ROUTABLE = frozenset({"World", "WorldCoasts"})


def approx_area(geom: BaseGeometry) -> float:
    """Approximate equal-area scale at the geometry centroid latitude."""
    if geom is None or geom.is_empty:
        return 0.0
    cy = float(geom.centroid.y)
    factor = math.cos(math.radians(cy))
    if abs(factor) < 1e-6:
        factor = 1e-6
    return abs(affinity.scale(geom, xfact=factor, yfact=1.0, origin=(0.0, 0.0)).area)


def rings_to_geom(rings: list[list[tuple[float, float]]]) -> Optional[BaseGeometry]:
    if not rings:
        return None
    parts: list[Polygon] = []
    # Osmosis: positive ring id = outer; "!N" = hole. dem_poly_filter drops the
    # hole marker into ring order: first ring outer, subsequent may be holes of
    # that outer OR additional outers after END. Treat first as outer+holes until
    # a ring that does not lie inside the current outer starts a new polygon.
    i = 0
    while i < len(rings):
        outer = rings[i]
        i += 1
        holes: list[list[tuple[float, float]]] = []
        while i < len(rings):
            cand = rings[i]
            try:
                poly_c = Polygon(cand)
                poly_o = Polygon(outer)
                if poly_o.is_valid and poly_c.is_valid and poly_o.contains(poly_c.representative_point()):
                    holes.append(cand)
                    i += 1
                    continue
            except Exception:
                pass
            break
        try:
            g: BaseGeometry = Polygon(outer, holes)
        except Exception:
            g = Polygon(outer)
        if not g.is_valid:
            g = make_valid(g)
        if isinstance(g, GeometryCollection):
            polys = [p for p in g.geoms if isinstance(p, (Polygon, MultiPolygon))]
            if not polys:
                continue
            g = unary_union(polys)
        if g is not None and not g.is_empty:
            parts.append(g)  # type: ignore[arg-type]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return unary_union(parts)


def load_poly_file(path: Path, simplify_deg: float = DEFAULT_SIMPLIFY_DEG) -> Optional[BaseGeometry]:
    text = path.read_text(encoding="utf-8", errors="replace")
    rings = parse_osmosis_poly(text)
    g = rings_to_geom(rings)
    if g is None or g.is_empty:
        return None
    if simplify_deg and simplify_deg > 0:
        g = g.simplify(simplify_deg, preserve_topology=True)
        if not g.is_valid:
            g = make_valid(g)
    return g


def humanize_region_id(region_id: str) -> str:
    """Best-effort display name from a Geofabrik-style path."""
    leaf = region_id.rstrip("/").split("/")[-1].replace("_", "-")
    parts = [p for p in leaf.split("-") if p]
    return " ".join(p[:1].upper() + p[1:] for p in parts) if parts else region_id


def bake_id_from_region(region_id: str, bake_id: Optional[str] = None) -> str:
    if bake_id:
        return bake_id
    return region_id.replace("/", "_").replace("-", "_")


def resolve_region_poly(
    region_id: str,
    bake_id: str,
    extracts_dir: Path,
    poly_cache: Optional[Path] = None,
) -> Optional[Path]:
    candidates = [
        extracts_dir / f"{bake_id}.poly",
        extracts_dir / f"{region_id.replace('/', '_')}.poly",
        extracts_dir / f"{Path(region_id).name}.poly",
    ]
    if poly_cache:
        candidates.extend(
            [
                poly_cache / f"{bake_id}.poly",
                poly_cache / f"{region_id.replace('/', '_')}.poly",
            ]
        )
    for c in candidates:
        if c.is_file():
            return c
    return None


@dataclass
class OverlapConfig:
    include_threshold_pct: float = DEFAULT_INCLUDE_THRESHOLD_PCT
    micro_threshold_pct: float = DEFAULT_MICRO_THRESHOLD_PCT
    mostly_outside_min_coverage_pct: float = DEFAULT_MOSTLY_OUTSIDE_MIN_COVERAGE_PCT
    mostly_outside_overspill_pct: float = DEFAULT_MOSTLY_OUTSIDE_OVERSPILL_PCT
    gap_warn_pct: float = DEFAULT_GAP_WARN_PCT
    max_leaves_warn: int = DEFAULT_MAX_LEAVES_WARN
    simplify_deg: float = DEFAULT_SIMPLIFY_DEG


@dataclass
class LeafIndex:
    names: list[str]
    geoms: list[BaseGeometry]
    areas: list[float]
    tree: STRtree


def build_leaf_index(borders_dir: Path, simplify_deg: float = DEFAULT_SIMPLIFY_DEG) -> LeafIndex:
    names: list[str] = []
    geoms: list[BaseGeometry] = []
    areas: list[float] = []
    for path in sorted(borders_dir.glob("*.poly")):
        if path.stem in NON_ROUTABLE:
            continue
        g = load_poly_file(path, simplify_deg=simplify_deg)
        if g is None or g.is_empty:
            continue
        names.append(path.stem)
        geoms.append(g)
        areas.append(approx_area(g))
    if not geoms:
        raise RuntimeError(f"no CoMaps border polygons in {borders_dir}")
    return LeafIndex(names=names, geoms=geoms, areas=areas, tree=STRtree(geoms))


def overlap_region_to_leaves(
    region_geom: BaseGeometry,
    leaf_index: LeafIndex,
    cfg: OverlapConfig,
) -> dict[str, Any]:
    region_area = approx_area(region_geom)
    if region_area <= 0:
        return {
            "union_coverage_pct": 0.0,
            "uncovered_pct": 100.0,
            "comaps_leaves": [],
            "error": "zero_region_area",
        }
    idxs = leaf_index.tree.query(region_geom)
    hits: list[dict[str, Any]] = []
    covered_parts: list[BaseGeometry] = []
    for i in idxs:
        leaf_g = leaf_index.geoms[int(i)]
        try:
            inter = region_geom.intersection(leaf_g)
        except Exception:
            continue
        if inter is None or inter.is_empty:
            continue
        ia = approx_area(inter)
        if ia <= 0:
            continue
        cov = 100.0 * ia / region_area
        if cov < cfg.micro_threshold_pct:
            continue
        la = leaf_index.areas[int(i)] or approx_area(leaf_g)
        overspill = 100.0 * max(0.0, 1.0 - (ia / la)) if la > 0 else 0.0
        mostly_outside = overspill >= cfg.mostly_outside_overspill_pct
        # Drop neighbouring-country / far-overspill noise: the leaf is mostly
        # elsewhere and covers only a thin sliver of this Navi region.
        if mostly_outside and cov < cfg.mostly_outside_min_coverage_pct:
            continue
        included = cov >= cfg.include_threshold_pct or cov >= cfg.micro_threshold_pct
        if not included:
            continue
        hits.append(
            {
                "leaf": leaf_index.names[int(i)],
                "coverage_of_region_pct": round(cov, 4),
                "overspill_pct": round(overspill, 4),
                "leaf_mostly_outside_region": mostly_outside,
                "include_reason": (
                    "threshold" if cov >= cfg.include_threshold_pct else "micro_land_overlap"
                ),
            }
        )
        if cov >= cfg.micro_threshold_pct:
            covered_parts.append(inter)
    hits.sort(key=lambda h: (-h["coverage_of_region_pct"], h["leaf"]))
    # Keep leaves that meet primary threshold OR micro (already filtered).
    # Prefer primary; drop micro if primary alone already covers well? No — keep all.
    union_pct = 0.0
    if covered_parts:
        try:
            u = unary_union(covered_parts)
            union_pct = min(100.0, 100.0 * approx_area(u) / region_area)
        except Exception:
            union_pct = min(100.0, sum(h["coverage_of_region_pct"] for h in hits))
    uncovered = max(0.0, 100.0 - union_pct)
    return {
        "union_coverage_pct": round(union_pct, 4),
        "uncovered_pct": round(uncovered, 4),
        "comaps_leaves": hits,
    }


def leaf_is_mostly_outside_noise(
    leaf_meta: dict[str, Any],
    cfg: OverlapConfig,
) -> bool:
    """True when a leaf should be dropped as mostly-outside low coverage."""
    cov = float(leaf_meta.get("coverage_of_region_pct") or 0.0)
    if "leaf_mostly_outside_region" in leaf_meta:
        mostly = bool(leaf_meta["leaf_mostly_outside_region"])
    elif leaf_meta.get("overspill_pct") is not None:
        mostly = float(leaf_meta["overspill_pct"]) >= cfg.mostly_outside_overspill_pct
    else:
        mostly = False
    return mostly and cov < cfg.mostly_outside_min_coverage_pct


def apply_mostly_outside_filter(
    regions: dict[str, dict[str, Any]],
    cfg: OverlapConfig,
) -> dict[str, list[str]]:
    """Drop mostly-outside low-coverage leaves from an existing migration map.

    Used to surgically repair published artifacts without re-running polygon
    overlap. Recalculates ``union_coverage_pct`` as a capped sum of remaining
    leaf coverages (overlap-unaware upper bound). Returns bake_id → dropped
    leaf ids.
    """
    dropped: dict[str, list[str]] = {}
    for rid, meta in regions.items():
        leaves = meta.get("comaps_leaves") or []
        kept: list[dict[str, Any]] = []
        gone: list[str] = []
        for leaf_meta in leaves:
            if leaf_is_mostly_outside_noise(leaf_meta, cfg):
                gone.append(leaf_meta["leaf"])
                continue
            kept.append(leaf_meta)
        if gone:
            dropped[rid] = gone
            meta["comaps_leaves"] = kept
            approx = min(
                100.0,
                sum(float(x.get("coverage_of_region_pct") or 0.0) for x in kept),
            )
            meta["union_coverage_pct"] = round(approx, 4)
            meta["uncovered_pct"] = round(max(0.0, 100.0 - approx), 4)
            meta["mostly_outside_filter_dropped"] = gone
    return dropped


def annotate_shared_leaves(regions: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    leaf_to_regions: dict[str, list[str]] = {}
    for rid, meta in regions.items():
        for leaf_meta in meta.get("comaps_leaves") or []:
            leaf = leaf_meta["leaf"]
            leaf_to_regions.setdefault(leaf, []).append(rid)
    shared = {leaf: sorted(rids) for leaf, rids in leaf_to_regions.items() if len(rids) > 1}
    for rid, meta in regions.items():
        for leaf_meta in meta.get("comaps_leaves") or []:
            leaf_meta["shared_with_navi_regions"] = [
                r for r in shared.get(leaf_meta["leaf"], []) if r != rid
            ]
    return shared


def review_flags(
    regions: dict[str, dict[str, Any]],
    cfg: OverlapConfig,
) -> dict[str, Any]:
    gaps = []
    too_many = []
    unmapped = []
    for rid, meta in sorted(regions.items()):
        leaves = meta.get("comaps_leaves") or []
        if not leaves:
            unmapped.append(rid)
        uncovered = float(meta.get("uncovered_pct") or 0.0)
        if uncovered > cfg.gap_warn_pct:
            gaps.append(
                {
                    "bake_id": rid,
                    "uncovered_pct": uncovered,
                    "union_coverage_pct": meta.get("union_coverage_pct"),
                    "leaf_count": len(leaves),
                }
            )
        if len(leaves) > cfg.max_leaves_warn:
            too_many.append(
                {
                    "bake_id": rid,
                    "leaf_count": len(leaves),
                    "leaves": [x["leaf"] for x in leaves],
                }
            )
    return {
        "gap_gt_pct": cfg.gap_warn_pct,
        "gaps": gaps,
        "max_leaves": cfg.max_leaves_warn,
        "too_many_leaves": too_many,
        "unmapped": unmapped,
    }


def _leaf_entry_from_node(node: dict[str, Any]) -> Optional[dict[str, Any]]:
    lid = node.get("id") or ""
    if not lid or lid in NON_ROUTABLE:
        return None
    return {
        "id": lid,
        "display_name": lid.split("_", 1)[-1] if "_" in lid else lid,
        "comaps_bytes": node.get("s"),
        "comaps_sha1_base64": node.get("sha1_base64"),
        "affiliations": node.get("affiliations") or [],
    }


def parse_countries_hierarchy(countries: dict[str, Any]) -> list[dict[str, Any]]:
    """Build country/group → leaves from countries.txt, preserving CoMaps parents.

    Top-level children of ``Countries`` become country entries. Nested groups
    (US states, etc.) appear as ``subgroups`` with ``parent_groups`` set.
    """

    def node_to_group(node: dict[str, Any], parents: list[str]) -> Optional[dict[str, Any]]:
        nid = node.get("id") or ""
        kids = node.get("g") or []
        if not nid or not kids:
            return None
        leaves: list[dict[str, Any]] = []
        subgroups: list[dict[str, Any]] = []
        for k in kids:
            if k.get("g"):
                sub = node_to_group(k, parents + [nid])
                if sub:
                    subgroups.append(sub)
            else:
                entry = _leaf_entry_from_node(k)
                if entry:
                    leaves.append(entry)
        if not leaves and not subgroups:
            return None
        leaf_count = len(leaves) + sum(int(s.get("leaf_count") or 0) for s in subgroups)
        return {
            "id": nid,
            "display_name": nid,
            "parent_groups": parents[:],
            "leaves": leaves,
            "subgroups": subgroups,
            "leaf_count": leaf_count,
        }

    out: list[dict[str, Any]] = []
    for k in countries.get("g") or []:
        kid_id = k.get("id") or ""
        if kid_id in NON_ROUTABLE:
            continue
        if k.get("g"):
            g = node_to_group(k, [])
            if g:
                out.append(g)
        else:
            entry = _leaf_entry_from_node(k)
            if entry:
                out.append(
                    {
                        "id": kid_id,
                        "display_name": kid_id,
                        "parent_groups": [],
                        "leaves": [entry],
                        "subgroups": [],
                        "leaf_count": 1,
                    }
                )
    return out


def enrich_country_groups_sizes(
    groups: list[dict[str, Any]],
    leaf_sizes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach MWM + sidecar sizes from catalog/index leaf entries (recursive)."""

    def enrich_one(g: dict[str, Any]) -> dict[str, Any]:
        leaves = []
        for leaf in g.get("leaves") or []:
            meta = leaf_sizes.get(leaf["id"]) or {}
            entry = dict(leaf)
            entry["mwm_bytes"] = (
                meta.get("mwm_bytes") or meta.get("comaps_bytes") or leaf.get("comaps_bytes")
            )
            entry["sidecar_bytes"] = meta.get("sidecar_bytes")
            entry["total_bytes"] = None
            if entry.get("mwm_bytes") is not None:
                entry["total_bytes"] = int(entry["mwm_bytes"]) + int(
                    entry.get("sidecar_bytes") or 0
                )
            leaves.append(entry)
        subgroups = [enrich_one(s) for s in (g.get("subgroups") or [])]
        ng = dict(g)
        ng["leaves"] = leaves
        ng["subgroups"] = subgroups
        ng["total_mwm_bytes"] = sum(int(x["mwm_bytes"] or 0) for x in leaves) + sum(
            int(s.get("total_mwm_bytes") or 0) for s in subgroups
        )
        ng["total_sidecar_bytes"] = sum(int(x.get("sidecar_bytes") or 0) for x in leaves) + sum(
            int(s.get("total_sidecar_bytes") or 0) for s in subgroups
        )
        ng["leaf_count"] = len(leaves) + sum(int(s.get("leaf_count") or 0) for s in subgroups)
        return ng

    return [enrich_one(g) for g in groups]


def apply_display_overrides(
    groups: list[dict[str, Any]],
    overrides: dict[str, Any],
) -> list[dict[str, Any]]:
    leaf_ov = (overrides.get("leaves") or {}) if overrides else {}

    def apply_one(g: dict[str, Any]) -> None:
        for leaf in g.get("leaves") or []:
            ov = leaf_ov.get(leaf["id"])
            if isinstance(ov, str):
                leaf["comaps_display_name"] = leaf.get("display_name")
                leaf["display_name"] = ov
                leaf["display_name_overridden"] = True
            elif isinstance(ov, dict) and ov.get("display_name"):
                leaf["comaps_display_name"] = leaf.get("display_name")
                leaf["display_name"] = ov["display_name"]
                leaf["display_name_overridden"] = True
                if ov.get("note"):
                    leaf["display_name_note"] = ov["note"]
        for sub in g.get("subgroups") or []:
            apply_one(sub)

    for g in groups:
        apply_one(g)
    return groups


def iter_country_group_leaves(groups: list[dict[str, Any]]) -> Iterable[str]:
    for g in groups:
        for leaf in g.get("leaves") or []:
            yield leaf["id"] if isinstance(leaf, dict) else leaf
        yield from iter_country_group_leaves(g.get("subgroups") or [])


def diff_migrations(prev: dict[str, Any], curr: dict[str, Any]) -> dict[str, Any]:
    """Diff two region-migration.json documents (leaf adds/removes/splits)."""
    prev_regions = (prev.get("regions") or {}) if prev else {}
    curr_regions = curr.get("regions") or {}
    prev_leaves: set[str] = set()
    curr_leaves: set[str] = set()
    for meta in prev_regions.values():
        for x in meta.get("comaps_leaves") or []:
            prev_leaves.add(x["leaf"] if isinstance(x, dict) else x)
    for meta in curr_regions.values():
        for x in meta.get("comaps_leaves") or []:
            curr_leaves.add(x["leaf"] if isinstance(x, dict) else x)
    mapping_changes = []
    all_ids = sorted(set(prev_regions) | set(curr_regions))
    for rid in all_ids:
        pl = {
            (x["leaf"] if isinstance(x, dict) else x)
            for x in (prev_regions.get(rid) or {}).get("comaps_leaves") or []
        }
        cl = {
            (x["leaf"] if isinstance(x, dict) else x)
            for x in (curr_regions.get(rid) or {}).get("comaps_leaves") or []
        }
        if pl != cl:
            mapping_changes.append(
                {
                    "bake_id": rid,
                    "added": sorted(cl - pl),
                    "removed": sorted(pl - cl),
                }
            )
    prev_cg_leaves = set(iter_country_group_leaves(prev.get("country_groups") or []))
    curr_cg_leaves = set(iter_country_group_leaves(curr.get("country_groups") or []))
    leaf_universe_prev = prev_cg_leaves or prev_leaves
    leaf_universe_curr = curr_cg_leaves or curr_leaves
    return {
        "schema": "navi-mwm-region-migration-diff/v1",
        "leaves_added": sorted(leaf_universe_curr - leaf_universe_prev),
        "leaves_removed": sorted(leaf_universe_prev - leaf_universe_curr),
        "mapping_changes": mapping_changes,
        "regions_added": sorted(set(curr_regions) - set(prev_regions)),
        "regions_removed": sorted(set(prev_regions) - set(curr_regions)),
    }


def to_publish_region_map(
    migration: dict[str, Any],
    limitation: str,
) -> dict[str, Any]:
    """Compact mapping consumed by publish-mwm.sh (region_leaf_mapping.json)."""
    regions_out: dict[str, Any] = {}
    for rid, meta in (migration.get("regions") or {}).items():
        regions_out[rid] = {
            "region_id": meta.get("region_id"),
            "display_name": meta.get("display_name"),
            "union_coverage_pct": meta.get("union_coverage_pct"),
            "uncovered_pct": meta.get("uncovered_pct"),
            "comaps_leaves": [
                {
                    "leaf": x["leaf"],
                    "coverage_of_region_pct": x.get("coverage_of_region_pct"),
                    "overspill_pct": x.get("overspill_pct"),
                    "shared_with_navi_regions": x.get("shared_with_navi_regions") or [],
                    "leaf_mostly_outside_region": x.get("leaf_mostly_outside_region", False),
                    "include_reason": x.get("include_reason"),
                }
                for x in meta.get("comaps_leaves") or []
            ],
        }
    return {
        "version": 2,
        "schema": "navi-mwm-region-leaf-mapping/v2",
        "pilot": "comaps-release",
        "limitation": limitation,
        "include_threshold_pct": migration.get("include_threshold_pct"),
        "micro_threshold_pct": migration.get("micro_threshold_pct"),
        "threshold_justification": migration.get("threshold_justification"),
        "navi_region_to_comaps_leaves": regions_out,
        "shared_leaves": migration.get("shared_leaves") or {},
        "review": migration.get("review") or {},
        "scope": "world",
        "comaps_release": migration.get("comaps_release"),
    }


THRESHOLD_JUSTIFICATION = (
    "Default include threshold is 0.5% of Navi region area: below that, "
    "numeric border noise and ocean/fjord slivers dominate, while still "
    "catching meaningful county-scale coverage. CoMaps borders are land "
    "polygons (no ocean), so any stable land overlap ≥ 0.05% is also kept "
    "as a micro_land_overlap proxy for border roads/places (stricter than "
    "ignoring those slivers; true OSM road sampling is not required for v1). "
    "Leaves with ≥50% of their area outside the Navi region are kept only "
    "when they still cover ≥25% of that region — otherwise neighbouring "
    "country coastal leaves (sea/fjord Geofabrik polys ∩ foreign land "
    "borders) pollute region_to_leaves (e.g. DK/SE under Østlandet)."
)
