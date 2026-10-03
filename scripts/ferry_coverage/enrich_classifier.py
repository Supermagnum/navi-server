#!/usr/bin/env python3
"""Extend ferries_all.csv with regions / in_pack / parent relations, and quantify
classifier gaps (road-class ferries, inherited relation access, duration formats).

Does not commit the CSV. Writes under scratch/out/.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_SCRIPTS = _SCRIPT_DIR.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ferry_coverage.common import (  # noqa: E402
    ACCESS_NO,
    ACCESS_YES,
    load_json,
    write_json,
)

FERRY_ROAD_CLASSES = {
    "motorway",
    "trunk",
    "primary",
    "secondary",
    "tertiary",
    "unclassified",
    "residential",
    "service",
}
FERRY_MOTOR_YES = ACCESS_YES | {"destination", "customers"}
FERRY_MOTOR_KEYS = ("motor_vehicle", "motorcar", "vehicle")
NORWAY_FIVE = (
    "europe_norway_vestlandet",
    "europe_norway_trondelag",
    "europe_norway_nord_norge",
    "europe_norway_sorlandet",
    "europe_norway_ostlandet",
)
OLD_DURATION_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$")
ISO_RE = re.compile(r"^P(?P<body>.+)$", re.I)
BARE_MIN_RE = re.compile(r"^\d+(?:\.\d+)?$")
LONG_H_RE = re.compile(r"^\d{3,}:\d{2}(?::\d{2})?$")


def is_no(raw: str | None) -> bool:
    return bool(raw) and raw.strip().lower() in ACCESS_NO


def is_ferry_motor_yes(raw: str | None) -> bool:
    return bool(raw) and raw.strip().lower() in FERRY_MOTOR_YES


def ferry_allowed_car_old(tags: dict[str, str]) -> bool:
    mv = (tags.get("motor_vehicle") or "").strip().lower()
    mc = (tags.get("motorcar") or "").strip().lower()
    return mv in ACCESS_YES or mc in ACCESS_YES


def ferry_allowed_car_new(tags: dict[str, str], parent_car: bool) -> bool:
    for key in ("motorcar", "motor_vehicle", "vehicle"):
        raw = tags.get(key) or ""
        if not raw:
            continue
        if is_ferry_motor_yes(raw):
            return True
        if is_no(raw):
            return False
        return False
    if is_no(tags.get("access")):
        return False
    ferry = (tags.get("ferry") or "").strip().lower()
    return ferry in FERRY_ROAD_CLASSES or parent_car


def duration_format_bucket(raw: str) -> str:
    s = raw.strip()
    if not s:
        return "empty"
    if ISO_RE.match(s) and ("T" in s.upper()):
        return "iso8601"
    if BARE_MIN_RE.match(s):
        return "bare_minutes"
    if OLD_DURATION_RE.match(s):
        return "h_mm_or_hh_mm_ss"
    if LONG_H_RE.match(s):
        first = int(s.split(":")[0])
        if first >= 60:
            return "mm_ss_first_ge_60"
        return "h_mm_three_digit_hours"
    if ":" in s:
        return "other_colon"
    return "other"


def parse_regions_conf(path: Path) -> list[str]:
    ids: list[str] = []
    if not path.is_file():
        return ids
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        rid = s.split("\t", 1)[0].split(None, 1)[0]
        ids.append(rid)
    return ids


def load_pack_way_ids(scratch: Path) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    scans = scratch / "out" / "pack_scans"
    if not scans.is_dir():
        return out
    for p in scans.glob("*.json"):
        if p.name.endswith(".compare.json"):
            continue
        bake_id = p.stem
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        ids: set[int] = set()
        for f in data.get("ferries") or []:
            wid = f.get("osm_way_id")
            if wid is not None:
                ids.add(int(wid))
        out[bake_id] = ids
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument(
        "--live-data",
        type=Path,
        default=Path("/media/navi/navi-server/data"),
    )
    args = ap.parse_args()
    scratch: Path = args.scratch
    live = args.live_data

    route = load_json(scratch / "overpass" / "route_ferry.json")
    tag_only = load_json(scratch / "overpass" / "ferry_tag_only.json")
    rel_members_path = scratch / "overpass" / "route_ferry_rel_members.json"

    by_key: dict[tuple[str, int], dict[str, Any]] = {}
    relations: list[dict[str, Any]] = []
    way_parents: dict[int, list[int]] = defaultdict(list)

    for el in (route.get("elements") or []) + (tag_only.get("elements") or []):
        osm_type = str(el.get("type"))
        osm_id = int(el.get("id"))
        tags = {str(k): str(v) for k, v in (el.get("tags") or {}).items()}
        rec = {"osm_type": osm_type, "osm_id": osm_id, "tags": tags, "members": el.get("members")}
        by_key[(osm_type, osm_id)] = rec
        if osm_type == "relation":
            relations.append(rec)

    if rel_members_path.is_file():
        rel_body = load_json(rel_members_path)
        for el in rel_body.get("elements") or []:
            if str(el.get("type")) != "relation":
                continue
            osm_id = int(el.get("id"))
            tags = {str(k): str(v) for k, v in (el.get("tags") or {}).items()}
            rec = {
                "osm_type": "relation",
                "osm_id": osm_id,
                "tags": tags,
                "members": el.get("members"),
            }
            relations.append(rec)
            by_key[("relation", osm_id)] = rec
        # Prefer the body dump (has members) when both files list the same id.
        rel_by_id = {r["osm_id"]: r for r in relations}
        relations = list(rel_by_id.values())

    parent_car: dict[int, bool] = {}
    for rel in relations:
        tags = rel["tags"]
        car = ferry_allowed_car_new(tags, False)
        parent_car[rel["osm_id"]] = car
        for mem in rel.get("members") or []:
            if not isinstance(mem, dict):
                continue
            if mem.get("type") != "way":
                continue
            wid = int(mem["ref"])
            way_parents[wid].append(rel["osm_id"])

    regions_by: dict[tuple[str, int], str] = {}
    by_region_csv = scratch / "out" / "ferries_by_region.csv"
    if by_region_csv.is_file():
        with by_region_csv.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                key = (row["osm_type"], int(row["osm_id"]))
                regions_by[key] = row.get("regions") or ""

    pack_ways = load_pack_way_ids(scratch)
    weekly_ids = parse_regions_conf(live / "regions.conf")

    src_csv = scratch / "out" / "ferries_all.csv"
    rows_out: list[dict[str, str]] = []
    gap_a: list[dict[str, Any]] = []
    inherit_would: list[dict[str, Any]] = []
    rejected_dur: Counter[str] = Counter()
    rejected_examples: dict[str, list[str]] = defaultdict(list)

    class_old = Counter()
    class_new = Counter()
    weekly_change: dict[str, Counter[str]] = defaultdict(Counter)

    with src_csv.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        extra = [
            "regions",
            "in_pack",
            "parent_relation_ids",
            "parent_car_capable",
            "classification_proposed",
        ]
        for col in extra:
            if col not in fieldnames:
                fieldnames.append(col)

        for row in reader:
            osm_type = row["osm_type"]
            osm_id = int(row["osm_id"])
            tags = {
                k: row.get(k) or ""
                for k in (
                    "motor_vehicle",
                    "motorcar",
                    "vehicle",
                    "hgv",
                    "access",
                    "foot",
                    "bicycle",
                    "ferry",
                    "duration",
                    "name",
                )
            }
            # Prefer Overpass tags when present (fuller).
            el = by_key.get((osm_type, osm_id))
            if el:
                tags = {**tags, **el["tags"]}

            regs = regions_by.get((osm_type, osm_id), "")
            region_list = [r for r in regs.split(";") if r]

            pids = way_parents.get(osm_id, []) if osm_type == "way" else []
            parent_ids_s = ";".join(str(i) for i in pids)
            any_parent_car = any(parent_car.get(i) for i in pids)

            old_car = row["classification"] == "car-capable"
            new_car = ferry_allowed_car_new(tags, any_parent_car) if osm_type != "relation" else ferry_allowed_car_new(tags, False)
            if osm_type == "relation":
                new_car = ferry_allowed_car_new(tags, False)

            proposed = "car-capable" if new_car else row["classification"]
            if new_car:
                proposed = "car-capable"
            elif row["classification"] == "car-capable":
                proposed = "passenger-bicycle-only"

            class_old[row["classification"]] += 1
            class_new[proposed] += 1

            in_pack = "no"
            if osm_type == "way":
                for bid in region_list or pack_ways:
                    if osm_id in pack_ways.get(bid, ()):
                        in_pack = "yes"
                        break
            else:
                mem_ids = [
                    int(m["ref"])
                    for m in (el.get("members") if el else []) or []
                    if isinstance(m, dict) and m.get("type") == "way"
                ]
                for bid in region_list or pack_ways:
                    pw = pack_ways.get(bid, set())
                    if any(mid in pw for mid in mem_ids):
                        in_pack = "yes"
                        break

            row["regions"] = regs
            row["in_pack"] = in_pack
            row["parent_relation_ids"] = parent_ids_s
            row["parent_car_capable"] = "yes" if any_parent_car else "no"
            row["classification_proposed"] = proposed
            rows_out.append(row)

            motor_unset = not any(tags.get(k) for k in FERRY_MOTOR_KEYS)
            hgv_unset = not (tags.get("hgv") or "")
            ferry_v = (tags.get("ferry") or "").strip().lower()
            if (
                osm_type == "way"
                and motor_unset
                and hgv_unset
                and ferry_v in FERRY_ROAD_CLASSES
                and row["classification"] != "car-capable"
            ):
                gap_a.append(
                    {
                        "osm_id": osm_id,
                        "name": tags.get("name") or row.get("name") or "",
                        "ferry": ferry_v,
                        "regions": region_list,
                        "in_pack": in_pack,
                        "classification": row["classification"],
                    }
                )

            if osm_type == "way" and motor_unset and not is_no(tags.get("access")):
                if any_parent_car and not new_car:
                    pass
                if any_parent_car and not ferry_allowed_car_old(tags):
                    if not (ferry_v in FERRY_ROAD_CLASSES):
                        inherit_would.append(
                            {
                                "osm_id": osm_id,
                                "name": tags.get("name") or "",
                                "parent_relation_ids": pids,
                                "regions": region_list,
                                "in_pack": in_pack,
                            }
                        )

            dur = (row.get("duration") or "").strip()
            if dur and row.get("duration_parseable") != "1":
                bucket = duration_format_bucket(dur)
                rejected_dur[bucket] += 1
                if len(rejected_examples[bucket]) < 8:
                    rejected_examples[bucket].append(dur)

            became = (not old_car) and new_car and osm_type == "way"
            left = old_car and (not new_car) and osm_type == "way"
            for bid in region_list:
                if bid in weekly_ids:
                    if became:
                        weekly_change[bid]["became_car"] += 1
                    if left:
                        weekly_change[bid]["left_car"] += 1
                    weekly_change[bid]["ways"] += 1

    out_csv = scratch / "out" / "ferries_all.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows_out)

    norway_lists: dict[str, list[dict[str, Any]]] = {}
    for bid in NORWAY_FIVE:
        norway_lists[bid] = sorted(
            [
                {
                    "osm_id": g["osm_id"],
                    "name": g["name"] or f"way/{g['osm_id']}",
                    "ferry": g["ferry"],
                    "in_pack": g["in_pack"],
                }
                for g in gap_a
                if bid in g["regions"]
            ],
            key=lambda x: (x["name"].lower(), x["osm_id"]),
        )

    world_examples = []
    want_names = ("losna", "oldeide", "baymouth", "tobermory")
    for g in gap_a:
        nl = (g["name"] or "").lower()
        if any(w in nl for w in want_names):
            world_examples.append(g)
    if len(world_examples) < 3:
        world_examples.extend(gap_a[:12])

    weekly_table = []
    for bid in weekly_ids:
        c = weekly_change.get(bid, Counter())
        weekly_table.append(
            {
                "bake_id": bid,
                "became_car_ways": int(c.get("became_car", 0)),
                "left_car_ways": int(c.get("left_car", 0)),
                "net": int(c.get("became_car", 0)) - int(c.get("left_car", 0)),
            }
        )

    gaps = {
        "csv_path": str(out_csv),
        "classification_old": dict(class_old),
        "classification_proposed": dict(class_new),
        "gap_a_road_class_no_motor_tags": {
            "world_count": len(gap_a),
            "examples": world_examples[:15],
            "norway": norway_lists,
            "norway_counts": {k: len(v) for k, v in norway_lists.items()},
        },
        "gap_b_inherit_parent_relation": {
            "ways_that_become_car_if_inherit": len(inherit_would),
            "examples": inherit_would[:20],
            "note": (
                "CSV ways with no motor_vehicle/motorcar/vehicle tags, not "
                "already car under the old yes-set, whose parent route=ferry "
                "relation is car-capable under the proposed rule, and without "
                "ferry=<road class> (those are counted in gap a)."
            ),
        },
        "gap_c_duration_rejected": {
            "total": sum(rejected_dur.values()),
            "by_format": rejected_dur.most_common(),
            "examples": {k: v for k, v in rejected_examples.items()},
        },
        "weekly_class_change_ways": weekly_table,
        "weekly_class_change_sum_became": sum(r["became_car_ways"] for r in weekly_table),
    }
    write_json(scratch / "out" / "classifier_gaps.json", gaps)
    print(json.dumps({
        "csv_path": str(out_csv),
        "gap_a": len(gap_a),
        "gap_b": len(inherit_would),
        "gap_c": sum(rejected_dur.values()),
        "weekly_became": gaps["weekly_class_change_sum_became"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
