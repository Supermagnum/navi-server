#!/usr/bin/env python3
"""Step 4: compare OSM car-capable ferries to published packs (ferry_pack_scan)."""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_SCRIPTS = _SCRIPT_DIR.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ferry_coverage.common import load_json, parseable_duration, write_json  # noqa: E402

PACKS_ROOT = Path("/media/navi/navi-server/data/published/packs")
EXTRACTS = Path("/media/navi/navi-server/data/scratch/extracts")

# Mega composites exceed weekly 16GiB RAM; skip pack load (still listed in region table).
SKIP_PACK_SCAN = {
    "asia_sea",
    "europe_dach",
    "europe_alps",
    "europe_britain_and_ireland",
    "europe_great_britain",
    "europe_ireland_and_northern_ireland",
    "north_america_us_west",
    "north_america_us_south",
    "north_america_us_midwest",
    "north_america_us_northeast",
    "north_america_us_pacific",
    "russia_siberian_fed_district",
    "russia_northwestern_fed_district",
    "russia_far_eastern_fed_district",
    "russia_central_fed_district",
    "russia_ural_fed_district",
    "russia_volga_fed_district",
    "russia_south_fed_district",
    "russia_north_caucasus_fed_district",
}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def latest_pack_dir(region_id: str, generation: str | None = None) -> Path | None:
    base = PACKS_ROOT / region_id
    if not base.is_dir():
        return None
    if generation:
        p = base / generation
        if p.is_dir() and any(p.glob("*.navi-manifest.json")):
            return p
    cands = []
    for p in base.iterdir():
        if not p.is_dir():
            continue
        if any(p.glob("*.navi-manifest.json")):
            cands.append(p)
    if not cands:
        return None
    return sorted(cands, key=lambda x: x.name)[-1]


def estimate_scan_seconds(pack_dir: Path) -> float:
    # Rough: ~0.04 s per MiB of pack dir from samples (vestlandet 531MiB/7s, egypt 1381/64s)
    try:
        # sum car tile sizes if available
        size = 0
        for p in pack_dir.glob("*navi-graph-car*"):
            size += p.stat().st_size
        if size <= 0:
            # du fallback via walk
            for root, _, files in os.walk(pack_dir):
                for f in files:
                    if "graph-car" in f:
                        size += (Path(root) / f).stat().st_size
        mi = size / (1024 * 1024)
        return max(5.0, mi * 0.05)
    except OSError:
        return 30.0


def match_ferry(
    osm: dict[str, Any],
    pack_ferries: list[dict[str, Any]],
    prox_m: float = 2500.0,
) -> dict[str, Any] | None:
    """Match OSM ferry to pack ferry.

    Published v9 packs regenerate edge ids as ``{src}-{tgt}-{i}`` on load, so
    OSM way ids are not retained. Match by endpoint proximity (and optional name).
    """
    eps = osm.get("endpoints") or []
    if len(eps) < 2:
        return None
    (a_lat, a_lon), (b_lat, b_lon) = eps[0], eps[1]
    osm_name = (osm.get("name") or "").strip().lower()
    best = None
    best_d = 1e18
    for pf in pack_ferries:
        d1 = haversine_m(a_lat, a_lon, pf["start_lat"], pf["start_lon"]) + haversine_m(
            b_lat, b_lon, pf["end_lat"], pf["end_lon"]
        )
        d2 = haversine_m(a_lat, a_lon, pf["end_lat"], pf["end_lon"]) + haversine_m(
            b_lat, b_lon, pf["start_lat"], pf["start_lon"]
        )
        d = min(d1, d2)
        # small bonus when names match exactly
        pf_name = (pf.get("name") or "").strip().lower()
        if osm_name and pf_name and osm_name == pf_name:
            d *= 0.5
        if d < best_d:
            best_d = d
            best = pf
    if best is not None and best_d <= 2 * prox_m:
        return best
    return None


def count_route_ferry_ways_in_pbf(pbf: Path) -> int | None:
    """Count route=ferry ways via osmium if available; else None."""
    if not pbf.is_file():
        return None
    try:
        # osmium tags-filter is fast; count lines
        r = subprocess.run(
            ["osmium", "tags-filter", str(pbf), "w/route=ferry", "-f", "opl", "-o", "-"],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if r.returncode != 0:
            return None
        return sum(1 for line in r.stdout.splitlines() if line.startswith("w"))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def run(
    scratch: Path,
    scan_bin: Path,
    max_regions: int | None = None,
) -> dict:
    assignment = load_json(scratch / "out" / "ferry_region_assignment.json")
    current = load_json(scratch / "current.json")
    current_by_bake: dict[str, dict[str, Any]] = {}
    for r in current.get("regions") or []:
        bid = r["bake_id"]
        prev = current_by_bake.get(bid)
        if prev is None or str(r.get("generation") or "") > str(
            prev.get("generation") or ""
        ):
            current_by_bake[bid] = r
    per_region = {r["bake_id"]: r for r in load_json(scratch / "out" / "per_region.json")}
    meta = load_json(scratch / "overpass" / "route_ferry.meta.json")
    osm_base = meta.get("osm_base")

    # regions with >=1 OSM car-capable ferry
    car_by_region: dict[str, list[dict[str, Any]]] = {}
    for f in assignment:
        if f.get("classification") != "car-capable":
            continue
        for bid in f.get("regions") or []:
            car_by_region.setdefault(bid, []).append(f)

    # Prefer scanning current.json regions (have packs). Skip pure composites without packs.
    targets: list[tuple[str, dict[str, Any]]] = []
    for bake_id, ferries in sorted(car_by_region.items(), key=lambda kv: -len(kv[1])):
        info = current_by_bake.get(bake_id)
        if not info:
            continue
        if bake_id in SKIP_PACK_SCAN or per_region.get(bake_id, {}).get("composite"):
            continue
        targets.append((bake_id, info))

    if max_regions is not None:
        targets = targets[:max_regions]

    # Duration estimate
    est_total = 0.0
    pack_dirs: dict[str, Path] = {}
    for bake_id, info in targets:
        pd = latest_pack_dir(info["region_id"], info.get("generation"))
        if pd is None:
            continue
        pack_dirs[bake_id] = pd
        est_total += estimate_scan_seconds(pd)

    est_path = scratch / "out" / "pack_scan_estimate.json"
    write_json(
        est_path,
        {
            "n_target_regions": len(targets),
            "n_with_pack_dir": len(pack_dirs),
            "estimated_seconds": round(est_total, 1),
            "estimated_hours": round(est_total / 3600.0, 2),
            "osm_base": osm_base,
        },
    )
    print(
        f"ESTIMATE: {len(pack_dirs)} packs, ~{est_total/60:.1f} min "
        f"(see {est_path})",
        flush=True,
    )

    out_dir = scratch / "out" / "pack_scans"
    out_dir.mkdir(parents=True, exist_ok=True)
    progress = scratch / "logs" / "pack_scan_progress.log"
    results: list[dict[str, Any]] = []

    for i, (bake_id, info) in enumerate(targets, 1):
        pd = pack_dirs.get(bake_id)
        line_prefix = f"[{i}/{len(targets)}] {bake_id}"
        if pd is None:
            msg = f"{line_prefix} SKIP no pack dir"
            print(msg, flush=True)
            with progress.open("a", encoding="utf-8") as pf:
                pf.write(msg + "\n")
            results.append({"bake_id": bake_id, "status": "no_pack"})
            continue

        json_out = out_dir / f"{bake_id}.json"
        t0 = time.time()
        cmd = [
            "nice",
            "-n",
            "19",
            "ionice",
            "-c3",
            str(scan_bin),
            str(pd),
            "--json-out",
            str(json_out),
        ]
        print(f"{line_prefix} scanning {pd.name} ...", flush=True)
        cp = subprocess.run(cmd, capture_output=True, text=True, check=False)
        dt = time.time() - t0
        summary_line = (cp.stdout or "").strip().splitlines()[:1]
        summary_line = summary_line[0] if summary_line else ""
        with progress.open("a", encoding="utf-8") as pf:
            pf.write(f"{line_prefix} {dt:.1f}s rc={cp.returncode} {summary_line}\n")
            if cp.returncode != 0:
                pf.write((cp.stderr or "")[:500] + "\n")

        if cp.returncode != 0 or not json_out.is_file():
            results.append(
                {
                    "bake_id": bake_id,
                    "status": "fail",
                    "stderr": (cp.stderr or "")[:500],
                    "pack_dir": str(pd),
                    "seconds": round(dt, 2),
                }
            )
            continue

        scan = load_json(json_out)
        osm_cars = car_by_region.get(bake_id, [])
        pack_ferries = scan.get("ferries") or []

        missing = []
        matched = 0
        matched_pack_ids: set[str] = set()
        for osm in osm_cars:
            hit = match_ferry(osm, pack_ferries)
            if hit is None:
                missing.append(
                    {
                        "osm_type": osm.get("osm_type"),
                        "osm_id": osm.get("osm_id"),
                        "name": osm.get("name"),
                        "duration": (osm.get("tags") or {}).get("duration"),
                    }
                )
            else:
                matched += 1
                matched_pack_ids.add(hit.get("edge_id") or "")

        # Pack ferries nearest to a non-car OSM ferry (and not nearer a car ferry)
        non_car = [
            f
            for f in assignment
            if f.get("classification") != "car-capable"
            and bake_id in (f.get("regions") or [])
            and len(f.get("endpoints") or []) >= 2
        ]
        wrongly_admitted = []
        for pf in pack_ferries:
            # skip reverse duplicates loosely by endpoints
            best_non = None
            best_non_d = 1e18
            for osm in non_car:
                hit_d = match_ferry(osm, [pf], prox_m=800.0)
                if hit_d is None:
                    continue
                # recompute distance
                eps = osm["endpoints"]
                (a_lat, a_lon), (b_lat, b_lon) = eps[0], eps[1]
                d1 = haversine_m(a_lat, a_lon, pf["start_lat"], pf["start_lon"]) + haversine_m(
                    b_lat, b_lon, pf["end_lat"], pf["end_lon"]
                )
                d2 = haversine_m(a_lat, a_lon, pf["end_lat"], pf["end_lon"]) + haversine_m(
                    b_lat, b_lon, pf["start_lat"], pf["start_lon"]
                )
                d = min(d1, d2)
                if d < best_non_d:
                    best_non_d = d
                    best_non = osm
            if best_non is None:
                continue
            # ensure no car-capable OSM is closer
            best_car_d = 1e18
            for osm in osm_cars:
                eps = osm.get("endpoints") or []
                if len(eps) < 2:
                    continue
                (a_lat, a_lon), (b_lat, b_lon) = eps[0], eps[1]
                d1 = haversine_m(a_lat, a_lon, pf["start_lat"], pf["start_lon"]) + haversine_m(
                    b_lat, b_lon, pf["end_lat"], pf["end_lon"]
                )
                d2 = haversine_m(a_lat, a_lon, pf["end_lat"], pf["end_lon"]) + haversine_m(
                    b_lat, b_lon, pf["start_lat"], pf["start_lon"]
                )
                best_car_d = min(best_car_d, d1, d2)
            if best_non_d < best_car_d and best_non_d <= 1600.0:
                wrongly_admitted.append(
                    {
                        "pack_edge_id": pf.get("edge_id"),
                        "osm_type": best_non.get("osm_type"),
                        "osm_id": best_non.get("osm_id"),
                        "name": best_non.get("name") or pf.get("name"),
                        "classification": best_non.get("classification"),
                        "match_m_sum": round(best_non_d, 1),
                        "tags": {
                            k: (best_non.get("tags") or {}).get(k)
                            for k in (
                                "motor_vehicle",
                                "motorcar",
                                "vehicle",
                                "access",
                                "foot",
                                "bicycle",
                            )
                        },
                    }
                )

        car_no_dur = sum(
            1
            for f in osm_cars
            if not parseable_duration((f.get("tags") or {}).get("duration"))
        )

        # PBF extract count if present
        pbf = EXTRACTS / f"{bake_id}-latest.osm.pbf"
        pbf_ferry_ways = count_route_ferry_ways_in_pbf(pbf) if pbf.is_file() else None

        terminals = [
            t
            for t in (scan.get("terminals") or [])
            if t.get("kind") in ("no_road", "tiny")
        ]

        row = {
            "bake_id": bake_id,
            "region_id": info.get("region_id"),
            "generation": info.get("generation"),
            "pack_dir": str(pd),
            "status": "ok",
            "seconds": round(dt, 2),
            "osm_car_capable": len(osm_cars),
            "pack_ferry_edges": scan.get("ferry_edges"),
            "matched_pack_edge_ids": len([x for x in matched_pack_ids if x]),
            "matched_osm_car": matched,
            "note": "OSM way ids not stored in published packs; matching by endpoint proximity",
            "missing_osm_car": missing,
            "missing_count": len(missing),
            "wrongly_admitted_passenger": wrongly_admitted,
            "wrongly_admitted_count": len(wrongly_admitted),
            "no_road": scan.get("no_road"),
            "tiny": scan.get("tiny"),
            "other_comp": scan.get("other_comp"),
            "terminals_no_road_tiny": terminals,
            "car_capable_without_parseable_duration": car_no_dur,
            "car_capable_without_parseable_duration_share": (
                round(car_no_dur / len(osm_cars), 4) if osm_cars else None
            ),
            "pack_generation": info.get("generation"),
            "overpass_osm_base": osm_base,
            "pbf_route_ferry_ways": pbf_ferry_ways,
            "pbf_path": str(pbf) if pbf.is_file() else None,
        }
        results.append(row)
        write_json(out_dir / f"{bake_id}.compare.json", row)

    write_json(scratch / "out" / "pack_compare.json", results)
    print(f"DONE pack compare -> {scratch / 'out' / 'pack_compare.json'}", flush=True)
    return {"n": len(results), "path": str(scratch / "out" / "pack_compare.json")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument(
        "--scan-bin",
        type=Path,
        default=Path("/tmp/navi-ferry-coverage-20261003/target/release/ferry_pack_scan"),
    )
    ap.add_argument("--max-regions", type=int, default=None)
    ap.add_argument("--estimate-only", action="store_true")
    args = ap.parse_args()
    if args.estimate_only:
        # reuse run's estimate by short-circuit — call estimate section only via max 0
        assignment = load_json(args.scratch / "out" / "ferry_region_assignment.json")
        current = load_json(args.scratch / "current.json")
        current_by_bake = {r["bake_id"]: r for r in current.get("regions") or []}
        car_regions = set()
        for f in assignment:
            if f.get("classification") == "car-capable":
                for bid in f.get("regions") or []:
                    if bid in current_by_bake:
                        car_regions.add(bid)
        est = 0.0
        n = 0
        for bid in car_regions:
            info = current_by_bake[bid]
            pd = latest_pack_dir(info["region_id"], info.get("generation"))
            if pd:
                est += estimate_scan_seconds(pd)
                n += 1
        print(json.dumps({"n_packs": n, "estimated_seconds": round(est, 1), "estimated_hours": round(est/3600, 2)}))
        return 0
    if not args.scan_bin.is_file():
        print(f"missing scan bin: {args.scan_bin}", file=sys.stderr)
        return 1
    run(args.scratch, args.scan_bin, args.max_regions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
