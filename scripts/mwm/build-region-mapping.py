#!/usr/bin/env python3
"""Build region-migration.json + country_groups + update region_leaf_mapping.json.

Reads (never writes) Geofabrik .poly extracts and CoMaps data/borders.
Optional soft-fetch of missing region .poly into --poly-cache only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))
sys.path.insert(0, str(SCRIPT_DIR.parent / "lib"))

from region_mapping import (  # noqa: E402
    DEFAULT_GAP_WARN_PCT,
    DEFAULT_INCLUDE_THRESHOLD_PCT,
    DEFAULT_MAX_LEAVES_WARN,
    DEFAULT_MICRO_THRESHOLD_PCT,
    DEFAULT_MOSTLY_OUTSIDE_MIN_COVERAGE_PCT,
    DEFAULT_MOSTLY_OUTSIDE_OVERSPILL_PCT,
    THRESHOLD_JUSTIFICATION,
    OverlapConfig,
    annotate_shared_leaves,
    apply_display_overrides,
    bake_id_from_region,
    build_leaf_index,
    diff_migrations,
    enrich_country_groups_sizes,
    humanize_region_id,
    iter_country_group_leaves,
    load_poly_file,
    overlap_region_to_leaves,
    parse_countries_hierarchy,
    resolve_region_poly,
    review_flags,
    to_publish_region_map,
)

REPO_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_OVERRIDES = REPO_ROOT / "config" / "mwm" / "catalog" / "display_name_overrides.json"
DEFAULT_REGION_MAP = REPO_ROOT / "config" / "mwm" / "catalog" / "region_leaf_mapping.json"
GEOFABRIK_BASE = os.environ.get("NAVI_GEOFABRIK_BASE", "https://download.geofabrik.de")


def load_published_regions(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    regions = data.get("regions") or []
    if not regions:
        raise SystemExit(f"no regions in {path}")
    return regions


def soft_fetch_poly(region_id: str, dest: Path) -> bool:
    url = f"{GEOFABRIK_BASE.rstrip('/')}/{region_id}.poly"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "navi-server-mwm-region-mapping/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
        if len(body) < 32 or b"END" not in body:
            return False
        dest.write_bytes(body)
        print(f"fetched {url} -> {dest}", flush=True)
        return True
    except Exception as exc:
        print(f"WARN soft-fetch failed {url}: {exc}", flush=True)
        return False


def load_leaf_sizes(catalog_path: Path | None, countries: dict) -> dict[str, dict]:
    sizes: dict[str, dict] = {}

    def walk(node: dict) -> None:
        kids = node.get("g") or []
        if not kids:
            lid = node.get("id")
            if lid:
                sizes[lid] = {
                    "comaps_bytes": node.get("s"),
                    "mwm_bytes": node.get("s"),
                }
            return
        for k in kids:
            walk(k)

    walk(countries)
    if catalog_path and catalog_path.is_file():
        cat = json.loads(catalog_path.read_text(encoding="utf-8"))
        for leaf in cat.get("leaves") or []:
            lid = leaf.get("id")
            if not lid:
                continue
            sizes.setdefault(lid, {})
            sizes[lid]["mwm_bytes"] = leaf.get("mwm_bytes") or leaf.get("comaps_bytes") or sizes[lid].get(
                "mwm_bytes"
            )
            sizes[lid]["sidecar_bytes"] = leaf.get("sidecar_bytes")
            sizes[lid]["comaps_bytes"] = leaf.get("comaps_bytes") or sizes[lid].get("comaps_bytes")
    return sizes


NAVI_REGION_DISPLAY = {
    "europe/norway/ostlandet": "Østlandet",
    "europe/norway/sorlandet": "Sørlandet",
    "europe/norway/vestlandet": "Vestlandet",
    "europe/norway/nord-norge": "Nord-Norge",
    "europe/norway/trondelag": "Trøndelag",
    "europe/norway/svalbard-janmayen": "Svalbard and Jan Mayen",
    "europe/norway/hedmark": "Hedmark",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--regions-json", type=Path, required=True, help="published .rkyv current.json")
    ap.add_argument("--extracts-dir", type=Path, required=True, help="Geofabrik .poly directory")
    ap.add_argument("--borders-dir", type=Path, required=True, help="CoMaps data/borders")
    ap.add_argument("--countries-txt", type=Path, required=True, help="CoMaps countries.txt")
    ap.add_argument("--out-dir", type=Path, required=True, help="Output directory for migration artifacts")
    ap.add_argument("--region-map-out", type=Path, default=DEFAULT_REGION_MAP)
    ap.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    ap.add_argument("--catalog", type=Path, default=None, help="Existing MWM catalog for sidecar sizes")
    ap.add_argument("--prev-migration", type=Path, default=None)
    ap.add_argument("--poly-cache", type=Path, default=None)
    ap.add_argument("--comaps-release", default="")
    ap.add_argument("--include-threshold-pct", type=float, default=DEFAULT_INCLUDE_THRESHOLD_PCT)
    ap.add_argument("--micro-threshold-pct", type=float, default=DEFAULT_MICRO_THRESHOLD_PCT)
    ap.add_argument(
        "--mostly-outside-min-coverage-pct",
        type=float,
        default=DEFAULT_MOSTLY_OUTSIDE_MIN_COVERAGE_PCT,
        help="Keep mostly-outside leaves only if they cover ≥ this %% of the region",
    )
    ap.add_argument(
        "--mostly-outside-overspill-pct",
        type=float,
        default=DEFAULT_MOSTLY_OUTSIDE_OVERSPILL_PCT,
        help="Treat a leaf as mostly-outside when overspill_pct ≥ this",
    )
    ap.add_argument("--gap-warn-pct", type=float, default=DEFAULT_GAP_WARN_PCT)
    ap.add_argument("--max-leaves-warn", type=int, default=DEFAULT_MAX_LEAVES_WARN)
    ap.add_argument("--limit", type=int, default=0, help="Process only first N regions (debug)")
    args = ap.parse_args()

    cfg = OverlapConfig(
        include_threshold_pct=args.include_threshold_pct,
        micro_threshold_pct=args.micro_threshold_pct,
        mostly_outside_min_coverage_pct=args.mostly_outside_min_coverage_pct,
        mostly_outside_overspill_pct=args.mostly_outside_overspill_pct,
        gap_warn_pct=args.gap_warn_pct,
        max_leaves_warn=args.max_leaves_warn,
    )
    regions = load_published_regions(args.regions_json)
    if args.limit and args.limit > 0:
        regions = regions[: args.limit]

    countries = json.loads(args.countries_txt.read_text(encoding="utf-8"))
    release = args.comaps_release or str(countries.get("v") or "")
    overrides = {}
    if args.overrides.is_file():
        overrides = json.loads(args.overrides.read_text(encoding="utf-8"))

    print(f"loading CoMaps borders from {args.borders_dir}", flush=True)
    t0 = time.time()
    leaf_index = build_leaf_index(args.borders_dir, simplify_deg=cfg.simplify_deg)
    print(f"loaded {len(leaf_index.names)} leaves in {time.time() - t0:.1f}s", flush=True)

    poly_cache = args.poly_cache
    mapped: dict[str, dict] = {}
    missing_poly: list[str] = []
    for i, reg in enumerate(regions, 1):
        region_id = reg["region_id"]
        bake_id = bake_id_from_region(region_id, reg.get("bake_id"))
        poly_path = resolve_region_poly(region_id, bake_id, args.extracts_dir, poly_cache)
        if poly_path is None and poly_cache is not None:
            dest = poly_cache / f"{bake_id}.poly"
            if soft_fetch_poly(region_id, dest):
                poly_path = dest
        if poly_path is None:
            missing_poly.append(bake_id)
            mapped[bake_id] = {
                "region_id": region_id,
                "display_name": NAVI_REGION_DISPLAY.get(region_id) or humanize_region_id(region_id),
                "union_coverage_pct": 0.0,
                "uncovered_pct": 100.0,
                "comaps_leaves": [],
                "error": "missing_poly",
            }
            continue
        geom = load_poly_file(poly_path, simplify_deg=cfg.simplify_deg)
        if geom is None or geom.is_empty:
            missing_poly.append(bake_id)
            mapped[bake_id] = {
                "region_id": region_id,
                "display_name": NAVI_REGION_DISPLAY.get(region_id) or humanize_region_id(region_id),
                "union_coverage_pct": 0.0,
                "uncovered_pct": 100.0,
                "comaps_leaves": [],
                "error": "empty_poly",
            }
            continue
        result = overlap_region_to_leaves(geom, leaf_index, cfg)
        result["region_id"] = region_id
        result["display_name"] = NAVI_REGION_DISPLAY.get(region_id) or humanize_region_id(region_id)
        result["poly_source"] = str(poly_path)
        mapped[bake_id] = result
        if i % 50 == 0 or i == len(regions):
            print(f"mapped {i}/{len(regions)}", flush=True)

    shared = annotate_shared_leaves(mapped)
    review = review_flags(mapped, cfg)
    review["missing_poly"] = missing_poly

    leaf_sizes = load_leaf_sizes(args.catalog, countries)
    country_groups = parse_countries_hierarchy(countries)
    country_groups = enrich_country_groups_sizes(country_groups, leaf_sizes)
    country_groups = apply_display_overrides(country_groups, overrides)

    cg_leaves = list(iter_country_group_leaves(country_groups))
    # Unique coverage of every routable leaf from countries.txt. CoMaps lists a
    # few disputed territories under multiple parents (Crimea, Abkhazia, …) —
    # those are multi_affiliated, not validation failures.
    dup_check: dict[str, int] = {}
    for lid in cg_leaves:
        dup_check[lid] = dup_check.get(lid, 0) + 1
    duplicates = sorted(k for k, v in dup_check.items() if v > 1)
    unique_cg = set(dup_check)

    def _countries_leaf_ids(node: dict, out: set[str]) -> None:
        kids = node.get("g") or []
        if not kids:
            lid = node.get("id")
            if lid and lid not in ("World", "WorldCoasts"):
                out.add(lid)
            return
        for k in kids:
            _countries_leaf_ids(k, out)

    countries_leaves: set[str] = set()
    _countries_leaf_ids(countries, countries_leaves)
    # Drop non-routable if present as groups
    countries_leaves -= {"World", "WorldCoasts"}
    missing_from_groups = sorted(countries_leaves - unique_cg)
    extra_in_groups = sorted(unique_cg - countries_leaves)

    limitation = (
        "Base MWMs come from a single CoMaps planet release; Navi publishes sidecars only. "
        "region_to_leaves maps frozen .rkyv bake_ids to CoMaps leaves for migration; "
        "country_groups is the primary region UI source."
    )

    migration = {
        "schema": "navi-mwm-region-migration/v1",
        "comaps_release": release,
        "generated_unix": int(time.time()),
        "include_threshold_pct": cfg.include_threshold_pct,
        "micro_threshold_pct": cfg.micro_threshold_pct,
        "mostly_outside_min_coverage_pct": cfg.mostly_outside_min_coverage_pct,
        "mostly_outside_overspill_pct": cfg.mostly_outside_overspill_pct,
        "threshold_justification": THRESHOLD_JUSTIFICATION,
        "gap_warn_pct": cfg.gap_warn_pct,
        "max_leaves_warn": cfg.max_leaves_warn,
        "region_count": len(mapped),
        "leaf_border_count": len(leaf_index.names),
        "regions": mapped,
        "shared_leaves": shared,
        "review": review,
        "validation": {
            "unmapped_regions": review["unmapped"],
            "multi_affiliated_leaves": duplicates,
            "multi_affiliated_note": (
                "CoMaps countries.txt lists disputed territories under more than "
                "one parent (e.g. Crimea, Abkhazia). The unique leaf set still "
                "covers every routable leaf exactly once for download inventory."
            ),
            "routable_leaves_missing_from_country_groups": missing_from_groups,
            "country_group_leaves_without_border": extra_in_groups,
            "every_region_has_leaf": len(review["unmapped"]) == 0,
            "every_routable_leaf_in_country_groups_unique": (
                len(missing_from_groups) == 0 and unique_cg == countries_leaves
            ),
            "countries_txt_routable_leaf_count": len(countries_leaves),
            "country_groups_unique_leaf_count": len(unique_cg),
        },
        "country_groups": country_groups,
        "display_name_overrides": {
            "leaves": (overrides.get("leaves") or {}),
            "likely_outdated_elsewhere": overrides.get("likely_outdated_elsewhere") or [],
        },
        "limitation": limitation,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    mig_path = args.out_dir / "region-migration.json"
    mig_path.write_text(json.dumps(migration, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {mig_path}", flush=True)

    cg_path = args.out_dir / "country_groups.json"
    cg_doc = {
        "schema": "navi-mwm-country-groups/v1",
        "comaps_release": release,
        "countries": country_groups,
        "display_name_overrides": migration["display_name_overrides"],
    }
    cg_path.write_text(json.dumps(cg_doc, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {cg_path}", flush=True)

    region_map = to_publish_region_map(migration, limitation)
    args.region_map_out.parent.mkdir(parents=True, exist_ok=True)
    args.region_map_out.write_text(json.dumps(region_map, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.region_map_out}", flush=True)

    prev_path = args.prev_migration
    if prev_path is None:
        cand = args.out_dir / "region-migration.prev.json"
        if cand.is_file():
            prev_path = cand
    if prev_path and prev_path.is_file():
        prev = json.loads(prev_path.read_text(encoding="utf-8"))
        diff = diff_migrations(prev, migration)
        diff_path = args.out_dir / "region-migration-diff.json"
        diff_path.write_text(json.dumps(diff, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {diff_path}", flush=True)
    else:
        print("no previous migration for diff", flush=True)

    print(
        "summary regions=%d shared_leaves=%d gaps=%d too_many=%d unmapped=%d missing_poly=%d"
        % (
            len(mapped),
            len(shared),
            len(review["gaps"]),
            len(review["too_many_leaves"]),
            len(review["unmapped"]),
            len(missing_poly),
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
