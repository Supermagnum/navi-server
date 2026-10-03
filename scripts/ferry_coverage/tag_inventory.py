#!/usr/bin/env python3
"""Step 2: tag inventory + classification from Overpass JSON."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Allow running as script from repo root or package dir
_SCRIPT_DIR = Path(__file__).resolve().parent
_SCRIPTS = _SCRIPT_DIR.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ferry_coverage.common import (  # noqa: E402
    INTEREST_KEYS,
    classify_ferry,
    ferry_allowed_for_bicycle,
    ferry_allowed_for_car,
    ferry_allowed_for_foot,
    iter_ferries_from_overpass,
    parseable_duration,
    write_json,
)


RULE_QUOTE = """
// pack-convert-core/src/routing/graph/builder.rs
pub(crate) fn ferry_allowed_for_profile(...) -> bool {
    match profile {
        RoutingProfile::Car | RoutingProfile::Truck => {
            tags.get("motor_vehicle").is_some_and(|v| access::is_access_yes(v))
                || tags.get("motorcar").is_some_and(|v| access::is_access_yes(v))
        }
        RoutingProfile::Foot | RoutingProfile::Bicycle => {
            !access::tags_forbid_mode(tags, profile.access_mode())
        }
    }
}
// is_access_yes: yes|true|1|designated|permissive|official
// Bare route=ferry without motor_vehicle/motorcar yes-set is NOT car-capable.
""".strip()


def inventory(scratch: Path) -> dict:
    route_path = scratch / "overpass" / "route_ferry.json"
    tag_path = scratch / "overpass" / "ferry_tag_only.json"
    meta_route = json.loads((scratch / "overpass" / "route_ferry.meta.json").read_text())
    meta_tag = json.loads((scratch / "overpass" / "ferry_tag_only.meta.json").read_text())

    records = list(iter_ferries_from_overpass(route_path, "route_ferry"))
    tag_only = list(iter_ferries_from_overpass(tag_path, "ferry_tag_only"))

    key_counts: Counter[str] = Counter()
    value_dist: dict[str, Counter[str]] = {k: Counter() for k in INTEREST_KEYS}
    class_counts: Counter[str] = Counter()
    unknown_combos: Counter[str] = Counter()
    duration_present = 0
    duration_parseable = 0
    by_type: Counter[str] = Counter()

    csv_path = scratch / "out" / "ferries_all.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "source",
                "osm_type",
                "osm_id",
                "classification",
                "name",
                "motor_vehicle",
                "motorcar",
                "vehicle",
                "hgv",
                "access",
                "foot",
                "bicycle",
                "ferry",
                "duration",
                "duration_parseable",
                "operator",
                "n_geom_pts",
            ]
        )
        for rec in records + tag_only:
            tags = rec.tags
            by_type[rec.osm_type] += 1
            for k in tags:
                key_counts[k] += 1
            for k in INTEREST_KEYS:
                if k in tags:
                    value_dist[k][tags[k]] += 1
            cls = classify_ferry(tags)
            class_counts[cls] += 1
            if cls == "unknown":
                combo = (
                    f"motor_vehicle={tags.get('motor_vehicle', '')}|"
                    f"motorcar={tags.get('motorcar', '')}|"
                    f"vehicle={tags.get('vehicle', '')}|"
                    f"access={tags.get('access', '')}|"
                    f"foot={tags.get('foot', '')}|"
                    f"bicycle={tags.get('bicycle', '')}"
                )
                unknown_combos[combo] += 1
            dur = tags.get("duration")
            if dur:
                duration_present += 1
                if parseable_duration(dur):
                    duration_parseable += 1
            w.writerow(
                [
                    rec.source,
                    rec.osm_type,
                    rec.osm_id,
                    cls,
                    tags.get("name", ""),
                    tags.get("motor_vehicle", ""),
                    tags.get("motorcar", ""),
                    tags.get("vehicle", ""),
                    tags.get("hgv", ""),
                    tags.get("access", ""),
                    tags.get("foot", ""),
                    tags.get("bicycle", ""),
                    tags.get("ferry", ""),
                    dur or "",
                    "1" if parseable_duration(dur) else "0",
                    tags.get("operator", ""),
                    len(rec.points),
                ]
            )

    # car-capable duration share (route=ferry only)
    car_route = [r for r in records if r.classification == "car-capable"]
    car_no_dur = sum(1 for r in car_route if not r.tags.get("duration"))
    car_dur_parse = sum(1 for r in car_route if parseable_duration(r.tags.get("duration")))

    out = {
        "osm_base_route_ferry": meta_route.get("osm_base"),
        "osm_base_ferry_tag_only": meta_tag.get("osm_base"),
        "rule_quote": RULE_QUOTE,
        "route_ferry_total": len(records),
        "route_ferry_by_type": dict(by_type),
        "ferry_tag_only_total": len(tag_only),
        "classification": dict(class_counts),
        "classification_route_ferry_only": dict(
            Counter(r.classification for r in records)
        ),
        "all_tag_keys": key_counts.most_common(),
        "value_distributions": {
            k: value_dist[k].most_common() for k in INTEREST_KEYS
        },
        "duration": {
            "present": duration_present,
            "parseable_H_MM_or_HH_MM_SS": duration_parseable,
            "car_capable_route_ferry": len(car_route),
            "car_capable_with_parseable_duration": car_dur_parse,
            "car_capable_without_duration": car_no_dur,
            "car_capable_without_duration_share": (
                round(car_no_dur / len(car_route), 4) if car_route else None
            ),
        },
        "unknown_tag_combos": unknown_combos.most_common(100),
        "csv_path": str(csv_path),
        "car_capable_note": (
            "Car-capable requires motor_vehicle or motorcar in the access yes-set "
            "(yes|true|1|designated|permissive|official). vehicle=yes alone is NOT enough."
        ),
    }
    write_json(scratch / "out" / "tag_inventory.json", out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", type=Path, required=True)
    args = ap.parse_args()
    out = inventory(args.scratch)
    print(json.dumps({k: out[k] for k in (
        "route_ferry_total", "ferry_tag_only_total", "classification",
        "duration", "csv_path"
    )}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
