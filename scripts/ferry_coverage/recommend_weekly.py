#!/usr/bin/env python3
"""Rank ferry-links weekly-add candidates under 8c / 16GB / 512GB constraints.

Never recommends a planet-wide bake. The Monday weekly job is the only regular
bake. Regions not already in regions.conf that fit the weekly budget are
proposed as weekly additions; over-budget / cut-off leaf regions get a
**targeted single-region bake now** (not a planet run).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_SCRIPTS = _SCRIPT_DIR.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ferry_coverage.common import load_json, write_json  # noqa: E402

# Host budget for the Monday weekly job (user constraint).
MAX_RAM_GB = 16.0
MAX_DISK_GB = 512.0
# Reasonable weekly wall window when converts are largely serial for large regions.
MAX_ADDED_CONVERT_H = 6.0
# Peak RSS headroom: leave ~2 GiB for OS / fetch / validate.
MAX_PEAK_RSS_MB = (MAX_RAM_GB - 2.0) * 1024.0
# Typical Copernicus 1° COG size observed on this host (~median of sample).
DEFAULT_DEM_MIB_PER_CELL = 28.0  # ~median Copernicus 1° COG on this host

# Never add these to weekly (composite / multi-country blobs / whole-US).
COMPOSITE_BLOCKLIST = {
    "asia_sea",
    "europe_dach",
    "europe_alps",
    "europe_britain_and_ireland",
    "europe_great_britain",
    "europe_ireland_and_northern_ireland",
    "africa",
    "antarctica",
    "asia",
    "australia_oceania",
    "central_america",
    "europe",
    "north_america",
    "south_america",
    "russia",
    "north_america_us",
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


def parse_regions_conf(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "\t" in s:
            rid, rest = s.split("\t", 1)
            src = rest.strip()
        else:
            parts = s.split(None, 1)
            if len(parts) < 2:
                continue
            rid, src = parts[0], parts[1]
        out[rid.strip()] = src.strip()
    return out


def planet_line_for(bake_id: str, planet: dict[str, str]) -> str | None:
    src = planet.get(bake_id)
    if not src:
        return None
    # Keep full trailing overrides if present in planet conf value
    return f"{bake_id}\t{src}"


def _stem(lat_f: int, lon_f: int) -> str:
    ns = "N" if lat_f >= 0 else "S"
    ew = "E" if lon_f >= 0 else "W"
    return f"{ns}{abs(lat_f):02d}{ew}{abs(lon_f):03d}"


def dem_cells_from_poly_bbox(bake_id: str, extracts: Path) -> list[str] | None:
    """Upper-bound 1° cells from extract .poly bbox (not road-filtered)."""
    poly = extracts / f"{bake_id}.poly"
    if not poly.is_file():
        return None
    try:
        from ferry_coverage.dem_poly_filter_fallback import load_poly_file
    except Exception:  # noqa: BLE001
        return None
    import math

    try:
        rings = load_poly_file(poly)
    except Exception:  # noqa: BLE001
        return None
    lons = [p[0] for r in rings for p in r]
    lats = [p[1] for r in rings for p in r]
    if not lats:
        return None
    stems = []
    for lat in range(math.floor(min(lats)), math.floor(max(lats)) + 1):
        for lon in range(math.floor(min(lons)), math.floor(max(lons)) + 1):
            stems.append(_stem(lat, lon))
    return stems


def dem_cell_info(
    bake_id: str, state_dir: Path, elev_dir: Path, extracts: Path
) -> dict[str, Any]:
    path = state_dir / "dem_cells" / f"{bake_id}.json"
    source = "road_cells_cache"
    stems: list[str] = []
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        stems = list(data.get("stems") or [])
        if not stems:
            for c in data.get("cells") or []:
                if isinstance(c, (list, tuple)) and len(c) == 2:
                    stems.append(_stem(int(c[0]), int(c[1])))
        cell_count = int(data.get("cell_count") or len(stems))
    else:
        bbox_stems = dem_cells_from_poly_bbox(bake_id, extracts)
        if not bbox_stems:
            return {
                "cell_count": None,
                "cells_known": False,
                "cells_source": None,
                "cells_missing_from_elev": None,
                "dem_added_mib_est": None,
                "stems": [],
            }
        stems = bbox_stems
        cell_count = len(stems)
        source = "poly_bbox_upper_bound"
    missing = [s for s in stems if not (elev_dir / "copernicus" / s).is_dir()]
    return {
        "cell_count": cell_count,
        "cells_known": True,
        "cells_source": source,
        "cells_missing_from_elev": len(missing),
        "dem_added_mib_est": round(len(missing) * DEFAULT_DEM_MIB_PER_CELL, 1),
        "stems": stems,
        "missing_stems_sample": missing[:20],
    }


def severity(row: dict[str, Any]) -> int:
    return int(row.get("no_road") or 0) + int(row.get("tiny") or 0)


def needs_ferry_links(row: dict[str, Any]) -> bool:
    return severity(row) > 0


def single_region_bake_outline(bake_id: str, *, ferry_links: bool = True) -> dict[str, Any]:
    """Command outline for a one-off bake (operator runs; analysis does not)."""
    env = [
        "cd /media/navi/navi-server",
        "set -a; source data/config.env; set +a",
    ]
    if ferry_links:
        env.append(f"export NAVI_FERRY_LINKS_REGIONS={bake_id}")
        env.append("# or: export NAVI_BAKE_FERRY_LINKS=1  # all regions in this convert")
    steps = env + [
        f"# One-off single-region bake (NOT a planet run). Prefetch DEM for this id only.",
        f"./scripts/run-weekly.sh --region {bake_id}",
        "# Equivalent pieces:",
        f"#   ./scripts/fetch-extracts.sh {bake_id}",
        f"#   ./scripts/fill-weekly-dem.sh   # or prefetch cells for {bake_id} only",
        f"#   ./scripts/convert-region.sh {bake_id}",
        f"#   ./scripts/validate-packs.sh --from-convert --region {bake_id}",
        f"#   ./scripts/publish-packs.sh --region {bake_id}",
    ]
    return {
        "action": "single_region_bake_now",
        "bake_id": bake_id,
        "commands": steps,
        "note": (
            "Run on the bake host when RAM/disk allow. Do not add this region to "
            "weekly until it fits the 8c/16GiB/512GiB budget alongside the existing "
            "weekly set — or after measuring a successful one-off convert."
        ),
    }


def run(scratch: Path, live_data: Path) -> dict[str, Any]:
    per = {r["bake_id"]: r for r in load_json(scratch / "out" / "per_region.json")}
    # current.json may list multiple generations per bake_id; keep newest.
    current: dict[str, dict[str, Any]] = {}
    for r in load_json(scratch / "current.json")["regions"]:
        bid = r["bake_id"]
        prev = current.get(bid)
        if prev is None or str(r.get("generation") or "") > str(prev.get("generation") or ""):
            current[bid] = r
    convert = load_json(scratch / "out" / "convert_metrics.json")
    weekly = parse_regions_conf(live_data / "regions.conf")
    planet = parse_regions_conf(live_data / "regions.planet.conf")
    pack_compare = []
    if (scratch / "out" / "pack_compare.json").is_file():
        pack_compare = load_json(scratch / "out" / "pack_compare.json")
    pc_by = {r["bake_id"]: r for r in pack_compare if r.get("status") == "ok"}

    state_dir = live_data / "state"
    elev_dir = live_data / "elevation"
    extracts = live_data / "scratch" / "extracts"

    # Baseline weekly resource from metrics for regions already in regions.conf
    weekly_convert_s = 0.0
    weekly_peak_rss = 0.0
    weekly_pack_bytes = 0
    weekly_dem_cells = 0
    for bid in weekly:
        m = convert.get(bid) or {}
        weekly_convert_s += (m.get("convert_ms") or 0) / 1000.0
        weekly_peak_rss = max(weekly_peak_rss, float(m.get("peak_rss_mb") or 0))
        cur = current.get(bid) or {}
        weekly_pack_bytes += int(cur.get("bytes") or 0)
        di = dem_cell_info(bid, state_dir, elev_dir, extracts)
        if di["cells_known"]:
            weekly_dem_cells += int(di["cell_count"] or 0)

    candidates: list[dict[str, Any]] = []
    # Prefer pack-scan rows; fall back to OSM-only car counts when scan incomplete
    assign = load_json(scratch / "out" / "ferry_region_assignment.json")
    car_counts: dict[str, int] = {}
    for f in assign:
        if f.get("classification") != "car-capable":
            continue
        for bid in f.get("regions") or []:
            car_counts[bid] = car_counts.get(bid, 0) + 1

    # Only published packs (current.json) or already-weekly ids — never invent planet runs.
    considered_ids = (set(car_counts) | set(pc_by)) & (set(current) | set(weekly))
    for bake_id in considered_ids:
        meta = per.get(bake_id, {})
        if meta.get("composite") or bake_id in COMPOSITE_BLOCKLIST:
            continue
        if re.search(r"_us_(west|south|midwest|northeast|pacific)$", bake_id):
            continue
        pc = pc_by.get(bake_id)
        osm_car = car_counts.get(bake_id, 0)
        if osm_car < 1 and not pc:
            continue
        # Need ferry links if audit says so; if scan pending, keep high-car leaves as watchlist
        if pc is not None and not needs_ferry_links(pc):
            if (pc.get("missing_count") or 0) == 0:
                continue

        m = convert.get(bake_id) or {}
        convert_ms = m.get("convert_ms")
        peak_rss = m.get("peak_rss_mb")
        cur = current.get(bake_id) or {}
        pack_bytes = cur.get("bytes")
        graph_ver = cur.get("graph_format_version")
        dem = dem_cell_info(bake_id, state_dir, elev_dir, extracts)

        fits_ram = peak_rss is None or float(peak_rss) <= MAX_PEAK_RSS_MB
        in_weekly = bake_id in weekly
        conf_line = None
        if not in_weekly:
            conf_line = planet_line_for(bake_id, planet)
            if conf_line is None and bake_id in weekly:
                conf_line = f"{bake_id}\t{weekly[bake_id]}"
            # hedmark / dalarna style already only in weekly
            if conf_line is None:
                # try reconstruct from current region_id
                rid = (cur.get("region_id") or "").strip()
                if rid:
                    conf_line = f"{bake_id}\tgeofabrik:{rid}"

        row = {
            "bake_id": bake_id,
            "in_weekly": in_weekly,
            "action": (
                "enable_NAVI_FERRY_LINKS_REGIONS"
                if in_weekly
                else "add_to_regions.conf_and_enable_ferry_links"
            ),
            "osm_car_capable": osm_car if pc is None else pc.get("osm_car_capable", osm_car),
            "no_road": None if pc is None else pc.get("no_road"),
            "tiny": None if pc is None else pc.get("tiny"),
            "missing_count": None if pc is None else pc.get("missing_count"),
            "pack_ferry_edges": None if pc is None else pc.get("pack_ferry_edges"),
            "scan_pending": pc is None,
            "convert_ms": convert_ms,
            "convert_s": None if convert_ms is None else round(float(convert_ms) / 1000.0, 1),
            "peak_rss_mb": peak_rss,
            "pack_bytes": pack_bytes,
            "pack_gib": None if pack_bytes is None else round(int(pack_bytes) / (1024**3), 2),
            "graph_format_version": graph_ver,
            "dem_cell_count": dem["cell_count"],
            "dem_cells_source": dem.get("cells_source"),
            "dem_cells_missing_from_elev": dem["cells_missing_from_elev"],
            "dem_added_mib_est": dem["dem_added_mib_est"],
            "fits_16gb_ram": fits_ram,
            "regions_conf_line": conf_line if not in_weekly else f"{bake_id}\t{weekly[bake_id]}",
            "severity": 0 if pc is None else severity(pc),
        }
        candidates.append(row)

    # Rank: boarding severity, then missing, then osm car; prefer known scan
    candidates.sort(
        key=lambda r: (
            0 if r["scan_pending"] else 1,
            -int(r["severity"] or 0),
            -int(r["missing_count"] or 0),
            -int(r["osm_car_capable"] or 0),
            r["bake_id"],
        ),
        reverse=False,
    )
    # Fix sort: scan_pending False(0) should come after known? Prefer known first
    candidates.sort(
        key=lambda r: (
            1 if r["scan_pending"] else 0,
            -int(r["severity"] or 0),
            -int(r["missing_count"] or 0),
            -int(r["osm_car_capable"] or 0),
            r["bake_id"],
        )
    )

    # Cut-off under budget: adding NEW weekly regions only (in_weekly already counted)
    added_convert_s = 0.0
    added_dem_mib = 0.0
    added_pack_gib = 0.0
    selected: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for r in candidates:
        if not needs_ferry_links(r) and not r["scan_pending"]:
            # only boarding-gap regions drive ferry-links enablement
            if (r.get("no_road") or 0) + (r.get("tiny") or 0) <= 0:
                continue
        def defer(reason: str) -> None:
            outline = single_region_bake_outline(r["bake_id"], ferry_links=True)
            deferred.append(
                {
                    **r,
                    "cut_reason": reason,
                    "action": "single_region_bake_now",
                    "single_region_bake": outline,
                }
            )

        if r["scan_pending"]:
            defer("pack_scan_pending")
            continue
        if not r["fits_16gb_ram"]:
            defer(f"peak_rss_mb>{MAX_PEAK_RSS_MB:.0f}")
            continue
        # already weekly: only ferry-links env, no added convert/disk for "add"
        if r["in_weekly"]:
            selected.append({**r, "cut_reason": None})
            continue
        add_s = float(r["convert_s"] or 0)
        add_dem = float(r["dem_added_mib_est"] or 0)
        add_pack = float(r["pack_gib"] or 0)
        # disk: published packs live under data/; weekly republish replaces in place roughly
        # but DEM cache is additive for new cells
        new_convert_h = (added_convert_s + add_s) / 3600.0
        new_dem_gib = (added_dem_mib + add_dem) / 1024.0
        # standing elev already 59G; ensure total elev + headroom stays sane vs 512G disk
        # Use additive DEM + pack publish scratch ~2x pack as rough disk delta
        disk_delta_gib = new_dem_gib + (added_pack_gib + add_pack) * 0.25
        if new_convert_h > MAX_ADDED_CONVERT_H:
            defer(f"added_convert_h>{MAX_ADDED_CONVERT_H}")
            continue
        if disk_delta_gib > 80:  # keep additive weekly growth modest vs 512G
            defer("added_disk_delta_too_large")
            continue
        # peak RSS of weekly becomes max(existing, new)
        if r["peak_rss_mb"] and float(r["peak_rss_mb"]) > MAX_PEAK_RSS_MB:
            defer("peak_rss")
            continue
        added_convert_s += add_s
        added_dem_mib += add_dem
        added_pack_gib += add_pack
        selected.append({**r, "cut_reason": None})

    ferry_links_ids = [r["bake_id"] for r in selected]
    add_conf_lines = [
        r["regions_conf_line"]
        for r in selected
        if not r["in_weekly"] and r.get("regions_conf_line")
    ]

    # Stale / below v9 — single-region bake now (never planet)
    stale = []
    for bid, cur in current.items():
        ver = cur.get("graph_format_version") or 0
        if ver < 9:
            outline = single_region_bake_outline(bid, ferry_links=False)
            stale.append(
                {
                    "bake_id": bid,
                    "graph_format_version": ver,
                    "generation": cur.get("generation"),
                    "in_weekly": bid in weekly,
                    "action": "single_region_bake_now",
                    "regions_conf_line": planet_line_for(bid, planet)
                    or (f"{bid}\t{weekly[bid]}" if bid in weekly else None),
                    "single_region_bake": outline,
                    "note": (
                        "Already weekly: one-off `./scripts/run-weekly.sh --region …` "
                        "to land v9 now. Not weekly: bake once now; only add to "
                        "regions.conf later if it fits the weekly budget."
                    ),
                }
            )

    # Already-weekly regions over RAM: ferry-links via one-off bake when host is free
    weekly_over = sorted(
        [
            {
                "bake_id": bid,
                "peak_rss_mb": (convert.get(bid) or {}).get("peak_rss_mb"),
                "action": "single_region_bake_now_when_host_allows",
                "single_region_bake": single_region_bake_outline(bid, ferry_links=True),
            }
            for bid in weekly
            if float((convert.get(bid) or {}).get("peak_rss_mb") or 0) > MAX_PEAK_RSS_MB
        ],
        key=lambda r: -(r["peak_rss_mb"] or 0),
    )

    out = {
        "budget": {
            "cores": 8,
            "ram_gb": MAX_RAM_GB,
            "disk_gb": MAX_DISK_GB,
            "max_peak_rss_mb": MAX_PEAK_RSS_MB,
            "max_added_convert_h": MAX_ADDED_CONVERT_H,
            "dem_mib_per_cell_est": DEFAULT_DEM_MIB_PER_CELL,
        },
        "weekly_baseline": {
            "n_regions": len(weekly),
            "convert_s_sum": round(weekly_convert_s, 1),
            "convert_h_sum_serial": round(weekly_convert_s / 3600.0, 2),
            "peak_rss_mb_max": weekly_peak_rss,
            "pack_bytes_sum": weekly_pack_bytes,
            "pack_gib_sum": round(weekly_pack_bytes / (1024**3), 2),
            "dem_cells_sum_known": weekly_dem_cells,
        },
        "selected": selected,
        "deferred": deferred,
        "totals_if_selected_adds": {
            "n_selected": len(selected),
            "n_new_weekly_regions": sum(1 for r in selected if not r["in_weekly"]),
            "added_convert_s": round(added_convert_s, 1),
            "added_convert_h": round(added_convert_s / 3600.0, 2),
            "added_dem_mib_est": round(added_dem_mib, 1),
            "added_dem_gib_est": round(added_dem_mib / 1024.0, 2),
            "added_pack_gib_published": round(added_pack_gib, 2),
            "weekly_convert_h_after_serial": round(
                (weekly_convert_s + added_convert_s) / 3600.0, 2
            ),
            "weekly_peak_rss_mb_after": max(
                [weekly_peak_rss]
                + [float(r["peak_rss_mb"] or 0) for r in selected]
            ),
        },
        "NAVI_FERRY_LINKS_REGIONS": ",".join(ferry_links_ids),
        "regions_conf_lines_to_add": add_conf_lines,
        "stale_or_below_v9": stale,
        "note": (
            "No planet-wide bake. Weekly (`data/regions.conf`) is the only regular bake. "
            "Leaf regions that need ferry links and fit the budget: add to weekly + set "
            "NAVI_FERRY_LINKS_REGIONS. Over-budget / cut-off leaves: run a targeted "
            "single-region bake now (`./scripts/run-weekly.sh --region <id>` with "
            "NAVI_FERRY_LINKS_REGIONS=<id>) so ferry-links/v9 land without a planet run; "
            "reconsider weekly membership only after they fit. Do not edit live config "
            "from this analysis."
        ),
        "weekly_already_over_ram_budget": weekly_over,
    }
    write_json(scratch / "out" / "weekly_ferry_recommendations.json", out)
    print(json.dumps({
        "n_selected": len(selected),
        "n_deferred": len(deferred),
        "NAVI_FERRY_LINKS_REGIONS": out["NAVI_FERRY_LINKS_REGIONS"],
        "regions_conf_lines_to_add": len(add_conf_lines),
        "totals": out["totals_if_selected_adds"],
        "stale": len(stale),
    }, indent=2))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--live-data", type=Path, default=Path("/media/navi/navi-server/data"))
    args = ap.parse_args()
    run(args.scratch, args.live_data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
