#!/usr/bin/env python3
"""Surgically drop mostly-outside low-coverage leaves from region-migration artifacts.

Does not re-run polygon overlap. Safe to use against a published
region-migration.json when the overlap algorithm gained the mostly-outside
gate and live catalogs still contain neighbouring-country noise
(e.g. Denmark/Sweden under europe/norway/ostlandet).

Rewrites:
  - region-migration.json (filtered leaves + refreshed shared/review)
  - region_leaf_mapping.json (compact publish map)
  - optional --catalog-out: only region_to_leaves + shared_leaves fields

country_groups.json is left untouched (built from countries.txt, not overlap).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from region_mapping import (  # noqa: E402
    DEFAULT_MOSTLY_OUTSIDE_MIN_COVERAGE_PCT,
    DEFAULT_MOSTLY_OUTSIDE_OVERSPILL_PCT,
    OverlapConfig,
    annotate_shared_leaves,
    apply_mostly_outside_filter,
    review_flags,
    to_publish_region_map,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--migration", type=Path, required=True, help="Input region-migration.json")
    ap.add_argument("--out-dir", type=Path, required=True, help="Output directory")
    ap.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help="Optional catalog/current.json to rewrite region_to_leaves + shared_leaves",
    )
    ap.add_argument(
        "--mostly-outside-min-coverage-pct",
        type=float,
        default=DEFAULT_MOSTLY_OUTSIDE_MIN_COVERAGE_PCT,
    )
    ap.add_argument(
        "--mostly-outside-overspill-pct",
        type=float,
        default=DEFAULT_MOSTLY_OUTSIDE_OVERSPILL_PCT,
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = OverlapConfig(
        mostly_outside_min_coverage_pct=args.mostly_outside_min_coverage_pct,
        mostly_outside_overspill_pct=args.mostly_outside_overspill_pct,
    )
    migration = json.loads(args.migration.read_text(encoding="utf-8"))
    regions = migration.get("regions") or {}
    dropped = apply_mostly_outside_filter(regions, cfg)
    n_leaves = sum(len(v) for v in dropped.values())
    print(
        f"filter would drop {n_leaves} leaves across {len(dropped)} regions "
        f"(min_coverage={cfg.mostly_outside_min_coverage_pct} "
        f"overspill>={cfg.mostly_outside_overspill_pct})",
        flush=True,
    )
    # Highlight Nordic / reported bug.
    for bake_id in (
        "europe_norway_ostlandet",
        "europe_norway_sorlandet",
        "europe_norway_nord_norge",
        "europe_norway_trondelag",
    ):
        if bake_id in dropped:
            print(f"  {bake_id}: dropped {dropped[bake_id]}", flush=True)
        elif bake_id in regions:
            kept = [x["leaf"] for x in regions[bake_id].get("comaps_leaves") or []]
            print(f"  {bake_id}: kept {kept}", flush=True)

    if args.dry_run:
        # Show a sample of foreign drops.
        sample = []
        for rid, leaves in sorted(dropped.items()):
            for leaf in leaves:
                pref = leaf.split("_", 1)[0]
                country = (regions[rid].get("region_id") or "").split("/")
                if len(country) >= 2 and pref.lower() != country[1].replace("-", " ").split()[0]:
                    sample.append((rid, leaf))
        print(f"sample foreign-ish drops ({min(20, len(sample))} of ~{len(sample)}):", flush=True)
        for rid, leaf in sample[:20]:
            print(f"  {rid} <- {leaf}", flush=True)
        return 0

    shared = annotate_shared_leaves(regions)
    review = review_flags(
        regions,
        OverlapConfig(
            gap_warn_pct=float(migration.get("gap_warn_pct") or 0.5),
            max_leaves_warn=int(migration.get("max_leaves_warn") or 8),
        ),
    )
    migration["regions"] = regions
    migration["shared_leaves"] = shared
    migration["review"] = review
    migration["mostly_outside_min_coverage_pct"] = cfg.mostly_outside_min_coverage_pct
    migration["mostly_outside_overspill_pct"] = cfg.mostly_outside_overspill_pct
    migration["repaired_unix"] = int(time.time())
    migration["repair"] = {
        "kind": "mostly_outside_filter",
        "dropped_leaf_count": n_leaves,
        "regions_touched": len(dropped),
        "source_migration": str(args.migration),
    }
    if migration.get("validation"):
        migration["validation"]["every_region_has_leaf"] = len(review["unmapped"]) == 0

    args.out_dir.mkdir(parents=True, exist_ok=True)
    mig_out = args.out_dir / "region-migration.json"
    mig_out.write_text(json.dumps(migration, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {mig_out}", flush=True)

    limitation = migration.get("limitation") or (
        "Base MWMs come from a single CoMaps planet release; Navi publishes sidecars only. "
        "region_to_leaves maps frozen .rkyv bake_ids to CoMaps leaves for migration; "
        "country_groups is the primary region UI source."
    )
    region_map = to_publish_region_map(migration, limitation)
    map_out = args.out_dir / "region_leaf_mapping.json"
    map_out.write_text(json.dumps(region_map, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {map_out}", flush=True)

    # Preserve country_groups from migration if present (unchanged by filter).
    if migration.get("country_groups") is not None:
        cg_out = args.out_dir / "country_groups.json"
        cg_doc = {
            "schema": "navi-mwm-country-groups/v1",
            "comaps_release": migration.get("comaps_release"),
            "countries": migration["country_groups"],
            "display_name_overrides": migration.get("display_name_overrides") or {},
        }
        cg_out.write_text(json.dumps(cg_doc, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {cg_out} (unchanged hierarchy)", flush=True)

    drop_report = args.out_dir / "mostly-outside-dropped.json"
    drop_report.write_text(
        json.dumps(
            {
                "dropped_leaf_count": n_leaves,
                "regions_touched": len(dropped),
                "dropped": dropped,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {drop_report}", flush=True)

    if args.catalog and args.catalog.is_file():
        cat = json.loads(args.catalog.read_text(encoding="utf-8"))
        region_to_leaves = {
            rid: [c["leaf"] for c in (meta.get("comaps_leaves") or [])]
            for rid, meta in regions.items()
        }
        cat["region_to_leaves"] = region_to_leaves
        cat["shared_leaves"] = shared
        if migration.get("limitation"):
            cat["limitation"] = migration["limitation"]
        cat_out = args.out_dir / "catalog.json"
        cat_out.write_text(json.dumps(cat, indent=2) + "\n", encoding="utf-8")
        print(
            f"wrote {cat_out} (region_to_leaves+shared_leaves only; "
            f"leaves/sidecars untouched)",
            flush=True,
        )
        ost = region_to_leaves.get("europe_norway_ostlandet") or []
        bad = [x for x in ost if x.startswith(("Denmark_", "Sweden_"))]
        assert not bad, f"ostlandet still has foreign leaves: {bad}"
        print(f"ostlandet OK: {ost}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
