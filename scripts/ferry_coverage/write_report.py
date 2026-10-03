#!/usr/bin/env python3
"""Write docs/ferry-coverage.md from analysis outputs (weekly-only recommendations)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SCRIPTS = _SCRIPT_DIR.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ferry_coverage.common import load_json  # noqa: E402
from ferry_coverage.recommend_weekly import run as recommend_run  # noqa: E402


def md_escape(s: str) -> str:
    return s.replace("|", "\\|")


def top_value_dist(items: list, limit: int = 12) -> str:
    if not items:
        return "_(none)_"
    lines = []
    for val, n in items[:limit]:
        lines.append(f"- `{md_escape(str(val))}`: {n}")
    if len(items) > limit:
        lines.append(f"- … +{len(items) - limit} more distinct values")
    return "\n".join(lines)


def run(scratch: Path, repo: Path, live_data: Path) -> Path:
    inv = load_json(scratch / "out" / "tag_inventory.json")
    per = load_json(scratch / "out" / "per_region.json")
    map_sum = load_json(scratch / "out" / "map_regions_summary.json")
    no_region = load_json(scratch / "out" / "ferries_no_region.json")
    pack_compare = []
    if (scratch / "out" / "pack_compare.json").is_file():
        pack_compare = load_json(scratch / "out" / "pack_compare.json")
    est = {}
    if (scratch / "out" / "pack_scan_estimate.json").is_file():
        est = load_json(scratch / "out" / "pack_scan_estimate.json")

    rec = recommend_run(scratch, live_data)
    gaps = {}
    if (scratch / "out" / "classifier_gaps.json").is_file():
        gaps = load_json(scratch / "out" / "classifier_gaps.json")
    ok_rows = [r for r in pack_compare if r.get("status") == "ok"]
    pc_by = {r["bake_id"]: r for r in ok_rows}

    lines: list[str] = []
    lines.append("# Ferry coverage analysis")
    lines.append("")
    lines.append(
        "Read-only analysis of OSM `route=ferry` (Overpass) against published Navi packs "
        "and `data/regions.conf`. Raw Overpass JSON stays under scratch (not committed)."
    )
    lines.append("")
    lines.append(
        "**Bake policy:** there is **no planet-wide bake**. The Monday weekly job "
        "(`data/regions.conf`) is the only regular bake. Regions that need ferry "
        "boarding links and **fit** the weekly budget should be **added to the weekly "
        "list** (exact conf lines below). Over-budget / cut-off leaves: run a "
        "**targeted single-region bake now** so ferry-links or v9 fixes land without "
        "waiting for (or depending on) a planet run; only promote them onto weekly "
        "later if the combined job still fits. Do not edit live config from this PR."
    )
    lines.append("")
    lines.append(f"- Scratch: `{scratch}`")
    lines.append(f"- Per-ferry CSV: `{inv.get('csv_path')}`")
    if gaps.get("csv_path"):
        lines.append(
            "- Extended CSV columns: `regions`, `in_pack`, `parent_relation_ids`, "
            "`parent_car_capable`, `classification_proposed` "
            f"(scratch only, not committed: `{gaps.get('csv_path')}`)"
        )
    lines.append(f"- Region assignment CSV: `{map_sum.get('csv_path')}`")
    lines.append(f"- Overpass `osm_base` (route=ferry): `{inv.get('osm_base_route_ferry')}`")
    if est:
        lines.append(
            f"- Pack scan estimate: ~{est.get('estimated_hours')} h "
            f"({est.get('n_with_pack_dir')} packs with car-capable OSM ferries)"
        )
    lines.append("")

    # ---- 1 tag inventory ----
    lines.append("## 1. Tag inventory")
    lines.append("")
    lines.append(f"- `route=ferry` objects (ways+relations): **{inv.get('route_ferry_total')}**")
    lines.append(f"- by OSM type: `{inv.get('route_ferry_by_type')}`")
    lines.append(
        f"- ways with `ferry=*` but not `route=ferry`: **{inv.get('ferry_tag_only_total')}**"
    )
    lines.append("")
    lines.append("### Classification (`ferry_allowed_for_profile`)")
    lines.append("")
    lines.append("Quoted rule from converter:")
    lines.append("")
    lines.append("```rust")
    lines.append(inv.get("rule_quote", ""))
    lines.append("```")
    lines.append("")
    lines.append(f"- Counts (all ingested objects): `{inv.get('classification')}`")
    lines.append(
        f"- `route=ferry` only: `{inv.get('classification_route_ferry_only')}`"
    )
    lines.append(f"- Note: {inv.get('car_capable_note')}")
    lines.append("")
    lines.append("#### Unknown tag combinations (top)")
    lines.append("")
    for combo, n in (inv.get("unknown_tag_combos") or [])[:30]:
        lines.append(f"- `{combo}`: {n}")
    lines.append("")
    lines.append("### Duration")
    lines.append("")
    dur = inv.get("duration") or {}
    lines.append(f"- duration tag present: {dur.get('present')}")
    lines.append(
        f"- parseable as `H:MM` / `HH:MM:SS`: {dur.get('parseable_H_MM_or_HH_MM_SS')}"
    )
    lines.append(
        f"- car-capable `route=ferry` without duration: "
        f"{dur.get('car_capable_without_duration')} "
        f"(share {dur.get('car_capable_without_duration_share')})"
    )
    lines.append("")
    if gaps:
        lines.append("## 1b. Classifier gaps (admission + duration)")
        lines.append("")
        lines.append(
            "Proposed car/truck admission (this PR): motor_vehicle / motorcar / "
            "vehicle in yes-set (`yes|true|1|designated|permissive|official|"
            "destination|customers`), **or** those keys absent, not explicitly "
            "denied (`no|private` on those keys or `access`), and either "
            "`ferry=<road class>` (`motorway|trunk|primary|secondary|tertiary|"
            "unclassified|residential|service`) or a parent `route=ferry` "
            "relation is car-capable. Explicit motor denial always wins. "
            "Untagged ferries with neither signal stay excluded."
        )
        lines.append("")
        lines.append(
            "Duration parser also accepts bare minutes, ISO 8601 `P[nD]T[nH][nM][nS]`, "
            "and two-part `MM:SS` when the first field is `>= 60` (otherwise `H:MM`). "
            "Unparseable values keep the length-based 10 km/h estimate."
        )
        lines.append("")
        lines.append(
            f"- Old classification counts: `{gaps.get('classification_old')}`"
        )
        lines.append(
            f"- Proposed classification counts: `{gaps.get('classification_proposed')}`"
        )
        lines.append("")
        ga = gaps.get("gap_a_road_class_no_motor_tags") or {}
        lines.append(
            "### (a) Road-class `ferry=*` with no motor_vehicle/motorcar/vehicle/hgv"
        )
        lines.append("")
        lines.append(
            "Ways with no `motor_vehicle` / `motorcar` / `vehicle` / `hgv` tag but "
            f"`ferry` in the road-class set: **{ga.get('world_count')}** worldwide "
            "(old classifier: passenger-bicycle-only)."
        )
        lines.append("")
        lines.append("World examples:")
        lines.append("")
        for ex in ga.get("examples") or []:
            lines.append(
                f"- way/{ex.get('osm_id')} {md_escape(str(ex.get('name') or ''))} "
                f"`ferry={ex.get('ferry')}` regions=`{';'.join(ex.get('regions') or [])}` "
                f"in_pack={ex.get('in_pack')}"
            )
        lines.append("")
        lines.append("All such ways in the five Norway regions (name + published pack):")
        lines.append("")
        for bid, items in (ga.get("norway") or {}).items():
            lines.append(f"#### `{bid}` ({len(items)})")
            lines.append("")
            if not items:
                lines.append("_none_")
                lines.append("")
                continue
            for it in items:
                pack = "yes (car ferry edge in published pack)" if it.get("in_pack") == "yes" else "no (not a car ferry edge in published pack)"
                lines.append(
                    f"- {md_escape(str(it.get('name') or ''))} "
                    f"(way/{it.get('osm_id')}, `ferry={it.get('ferry')}`) — {pack}"
                )
            lines.append("")
        gb = gaps.get("gap_b_inherit_parent_relation") or {}
        lines.append("### (b) Untagged members of a car-capable `route=ferry` relation")
        lines.append("")
        lines.append(
            f"Ways with no motor tags that would become car-capable by inheriting "
            f"a car-capable parent relation: **{gb.get('ways_that_become_car_if_inherit')}**."
        )
        lines.append("")
        for ex in (gb.get("examples") or [])[:15]:
            lines.append(
                f"- way/{ex.get('osm_id')} {md_escape(str(ex.get('name') or ''))} "
                f"parents={ex.get('parent_relation_ids')} in_pack={ex.get('in_pack')}"
            )
        lines.append("")
        gc = gaps.get("gap_c_duration_rejected") or {}
        lines.append("### (c) Duration values the old parser rejects")
        lines.append("")
        lines.append(f"Total rejected (tag present, old `H:MM`/`HH:MM:SS` only): **{gc.get('total')}**")
        lines.append("")
        lines.append("| format | count | examples |")
        lines.append("|---|---:|---|")
        examples = gc.get("examples") or {}
        for fmt, n in gc.get("by_format") or []:
            exs = ", ".join(f"`{md_escape(str(x))}`" for x in (examples.get(fmt) or [])[:6])
            lines.append(f"| `{fmt}` | {n} | {exs} |")
        lines.append("")
        lines.append("### Weekly region class-change counts (inventory, not a world bake)")
        lines.append("")
        lines.append(
            "Ferry **ways** whose proposed car class differs from the old "
            "`motor_vehicle`/`motorcar` yes-set. Counted per weekly `regions.conf` "
            "id (a way on a regional boundary may appear in more than one row)."
        )
        lines.append("")
        lines.append("| bake_id | became car | left car | net |")
        lines.append("|---|---:|---:|---:|")
        changed = [r for r in (gaps.get("weekly_class_change_ways") or []) if r.get("became_car_ways") or r.get("left_car_ways")]
        for r in sorted(changed, key=lambda x: -x.get("became_car_ways", 0)):
            lines.append(
                f"| {r['bake_id']} | {r.get('became_car_ways')} | "
                f"{r.get('left_car_ways')} | {r.get('net')} |"
            )
        if not changed:
            lines.append("| _(none)_ | 0 | 0 | 0 |")
        lines.append("")
        lines.append(
            f"Sum of became-car way counts across weekly rows: "
            f"**{gaps.get('weekly_class_change_sum_became')}** "
            "(not unique worldwide)."
        )
        lines.append("")
    lines.append("### Tag key totals (every key)")
    lines.append("")
    for k, n in inv.get("all_tag_keys") or []:
        lines.append(f"- `{k}`: {n}")
    lines.append("")
    lines.append("### Value distributions (selected keys)")
    lines.append("")
    for key in (
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
    ):
        lines.append(f"#### `{key}`")
        lines.append("")
        lines.append(top_value_dist((inv.get("value_distributions") or {}).get(key) or []))
        lines.append("")

    # ---- 2 per-region ----
    lines.append("## 2. Per-region ferry table")
    lines.append("")
    lines.append(
        f"Shapes: {map_sum.get('shapes_with_poly')} with `.poly`, "
        f"{map_sum.get('shapes_bbox_only')} bbox-only, "
        f"{map_sum.get('shapes_missing')} missing geometry. "
        f"Ferries in no region: **{map_sum.get('n_no_region')}** "
        f"(scratch `out/ferries_no_region.json`)."
    )
    lines.append("")
    lines.append(
        "| bake_id | in weekly | composite | route=ferry | car | pax/bike | unknown | "
        "pack ferry edges | missing OSM car | no_road | tiny | wrong pax |"
    )
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    table_rows = sorted(
        [r for r in per if (r.get("route_ferry") or 0) > 0],
        key=lambda r: (-(r.get("car_capable") or 0), r["bake_id"]),
    )
    for r in table_rows:
        pc = pc_by.get(r["bake_id"], {})
        lines.append(
            "| {bid} | {w} | {c} | {rf} | {car} | {pax} | {unk} | {pfe} | {miss} | {nr} | {ty} | {wp} |".format(
                bid=r["bake_id"],
                w="Y" if r.get("in_weekly") else "",
                c="Y" if r.get("composite") else "",
                rf=r.get("route_ferry") or 0,
                car=r.get("car_capable") or 0,
                pax=r.get("passenger_bicycle_only") or 0,
                unk=r.get("unknown") or 0,
                pfe=pc.get("pack_ferry_edges", ""),
                miss=pc.get("missing_count", ""),
                nr=pc.get("no_road", ""),
                ty=pc.get("tiny", ""),
                wp=pc.get("wrongly_admitted_count", ""),
            )
        )
    lines.append("")

    # ---- 3 recommendations ----
    lines.append("## 3. `NAVI_FERRY_LINKS_REGIONS` + weekly `regions.conf` adds")
    lines.append("")
    lines.append(
        "Candidates need boarding-island fixes (`no_road` / `tiny` from "
        "`ferry_terminal_audit` / `ferry_pack_scan`). Regions **not** already in "
        "`data/regions.conf` are recommended as **weekly additions** (not a separate "
        "planet track). Composites / mega-blobs that exceed the 16 GiB weekly RAM "
        "budget are deferred."
    )
    lines.append("")
    budget = rec.get("budget") or {}
    base = rec.get("weekly_baseline") or {}
    totals = rec.get("totals_if_selected_adds") or {}
    lines.append("### Weekly host budget")
    lines.append("")
    lines.append(
        f"- Target: **{budget.get('cores')} cores / {budget.get('ram_gb')} GiB RAM / "
        f"{budget.get('disk_gb')} GiB disk**"
    )
    lines.append(
        f"- Peak RSS ceiling used here: **{budget.get('max_peak_rss_mb')} MiB** "
        "(leave ~2 GiB for OS/fetch/validate)"
    )
    lines.append(
        f"- Max added serial convert time for new weekly regions: "
        f"**{budget.get('max_added_convert_h')} h**"
    )
    lines.append("")
    lines.append("### Current weekly baseline (`regions.conf`)")
    lines.append("")
    lines.append(f"- Regions: {base.get('n_regions')}")
    lines.append(
        f"- Serial convert sum (from planet-leaves PASS logs): "
        f"**{base.get('convert_h_sum_serial')} h** ({base.get('convert_s_sum')} s)"
    )
    lines.append(f"- Max peak RSS among weekly: **{base.get('peak_rss_mb_max')} MiB**")
    lines.append(f"- Sum published pack size: **{base.get('pack_gib_sum')} GiB**")
    lines.append(
        f"- Known DEM road cells (sum of caches): {base.get('dem_cells_sum_known')}"
    )
    over = rec.get("weekly_already_over_ram_budget") or []
    if over:
        lines.append(
            "- Already over ~14 GiB peak RSS on weekly list (host budget stress): "
            + ", ".join(
                f"`{r['bake_id']}` ({r.get('peak_rss_mb')} MiB)" for r in over
            )
        )
        lines.append(
            "  Prefer a one-off `run-weekly.sh --region …` with "
            "`NAVI_FERRY_LINKS_REGIONS=<id>` when the host is free, rather than "
            "assuming Monday can absorb more peak RSS."
        )
    lines.append("")
    lines.append("### Exact env line")
    lines.append("")
    lines.append("```bash")
    lines.append(f"NAVI_FERRY_LINKS_REGIONS={rec.get('NAVI_FERRY_LINKS_REGIONS')}")
    lines.append("```")
    lines.append("")
    lines.append("### Exact `data/regions.conf` lines to add")
    lines.append("")
    lines.append("Do **not** edit the live config from this analysis agent. Copy manually:")
    lines.append("")
    add_lines = rec.get("regions_conf_lines_to_add") or []
    if add_lines:
        lines.append("```")
        for ln in add_lines:
            lines.append(ln)
        lines.append("```")
    else:
        lines.append("_No new weekly regions in the selected set (all selected already weekly), "
                     "or pack scan still incomplete._")
    lines.append("")
    lines.append("### Selected candidates (with resource columns)")
    lines.append("")
    lines.append(
        "| bake_id | already weekly | convert | peak RSS MiB | pack GiB | DEM cells "
        "(source) | DEM missing | DEM add MiB est | no_road | tiny | action |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for r in rec.get("selected") or []:
        dem_src = r.get("dem_cells_source") or "?"
        dem_n = r.get("dem_cell_count")
        dem_col = f"{dem_n} ({dem_src})" if dem_n is not None else "—"
        lines.append(
            "| {bid} | {w} | {cs} | {rss} | {pg} | {dem} | {miss} | {dadd} | {nr} | {ty} | {act} |".format(
                bid=r["bake_id"],
                w="Y" if r.get("in_weekly") else "",
                cs=r.get("convert_s") if r.get("convert_s") is not None else "—",
                rss=r.get("peak_rss_mb") if r.get("peak_rss_mb") is not None else "—",
                pg=r.get("pack_gib") if r.get("pack_gib") is not None else "—",
                dem=dem_col,
                miss=r.get("dem_cells_missing_from_elev")
                if r.get("dem_cells_missing_from_elev") is not None
                else "—",
                dadd=r.get("dem_added_mib_est")
                if r.get("dem_added_mib_est") is not None
                else "—",
                nr=r.get("no_road") if r.get("no_road") is not None else "—",
                ty=r.get("tiny") if r.get("tiny") is not None else "—",
                act=r.get("action"),
            )
        )
    lines.append("")
    lines.append("### Total effect of selected adds")
    lines.append("")
    lines.append(
        f"- New weekly regions: **{totals.get('n_new_weekly_regions')}** "
        f"(plus ferry-links enable for already-weekly selected)"
    )
    lines.append(
        f"- Added serial convert: **{totals.get('added_convert_h')} h** "
        f"({totals.get('added_convert_s')} s)"
    )
    lines.append(
        f"- Weekly serial convert after: **{totals.get('weekly_convert_h_after_serial')} h**"
    )
    lines.append(
        f"- Weekly peak RSS after (max): **{totals.get('weekly_peak_rss_mb_after')} MiB**"
    )
    lines.append(
        f"- Added DEM cache (missing stems × ~{budget.get('dem_mib_per_cell_est')} MiB): "
        f"**{totals.get('added_dem_gib_est')} GiB** "
        f"({totals.get('added_dem_mib_est')} MiB)"
    )
    lines.append(
        f"- Published pack size of new regions (reference): "
        f"**{totals.get('added_pack_gib_published')} GiB**"
    )
    lines.append("")
    lines.append("### Deferred / cut-off → single-region bake now")
    lines.append("")
    lines.append(
        "These leaves need ferry links (or are still scan-pending) but **do not** "
        "fit adding to weekly in one shot. Recommendation: **targeted single-region "
        "bake now** (never a planet run). After a successful one-off convert, "
        "re-measure RSS/DEM/disk before promoting onto `regions.conf`."
    )
    lines.append("")
    deferred = rec.get("deferred") or []
    if not deferred:
        lines.append("_None._")
    else:
        lines.append(
            "| bake_id | convert s | peak RSS | pack GiB | DEM cells | cut reason | action |"
        )
        lines.append("|---|---:|---:|---:|---:|---|---|")
        for r in deferred[:60]:
            lines.append(
                f"| {r['bake_id']} | {r.get('convert_s') or '—'} | "
                f"{r.get('peak_rss_mb') or '—'} | {r.get('pack_gib') or '—'} | "
                f"{r.get('dem_cell_count') or '—'} | {r.get('cut_reason')} | "
                f"{r.get('action') or 'single_region_bake_now'} |"
            )
        if len(deferred) > 60:
            lines.append(f"| … | | | | | | +{len(deferred) - 60} more |")
        lines.append("")
        lines.append("#### One-off bake outline (per cut-off leaf)")
        lines.append("")
        lines.append("```bash")
        lines.append("cd /media/navi/navi-server")
        lines.append("set -a; source data/config.env; set +a")
        lines.append("export NAVI_FERRY_LINKS_REGIONS=<bake_id>")
        lines.append("./scripts/run-weekly.sh --region <bake_id>")
        lines.append("```")
        lines.append("")
        # Show a few concrete examples from deferred
        examples = [r for r in deferred if r.get("cut_reason") != "pack_scan_pending"][:8]
        if examples:
            lines.append("Examples from this cut-off list:")
            lines.append("")
            for r in examples:
                lines.append(f"- `{r['bake_id']}` — reason `{r.get('cut_reason')}`:")
                lines.append("  ```bash")
                lines.append(f"  export NAVI_FERRY_LINKS_REGIONS={r['bake_id']}")
                lines.append(f"  ./scripts/run-weekly.sh --region {r['bake_id']}")
                lines.append("  ```")
            lines.append("")
    lines.append("")
    lines.append(f"_{rec.get('note')}_")
    lines.append("")

    # ---- 4 converter problems ----
    lines.append("## 4. Converter problem candidates (boarding islands)")
    lines.append("")
    lines.append(
        "OSM ids / pack edges where packs disagree with `ferry_allowed_for_profile` "
        "or expected car set. Published packs reconstruct edge ids as `src-tgt-i`; "
        "matching uses endpoint proximity. **Admission + duration** are fixed in "
        "this PR (`pack-convert-core`). Boarding-island `no_road`/`tiny` stays with "
        "the separate ferry-links opt-in."
    )
    lines.append("")
    problems = []
    for r in ok_rows:
        for w in r.get("wrongly_admitted_passenger") or []:
            problems.append(
                {
                    "bake_id": r["bake_id"],
                    "kind": "wrongly_admitted_passenger",
                    **w,
                }
            )
        for m in r.get("missing_osm_car") or []:
            problems.append(
                {
                    "bake_id": r["bake_id"],
                    "kind": "missing_from_pack",
                    **m,
                }
            )
    if not problems:
        lines.append("_None recorded yet (or pack scan incomplete)._")
    else:
        for p in problems[:200]:
            lines.append(
                f"- `{p.get('bake_id')}` {p.get('kind')} "
                f"{p.get('osm_type', 'way')}/{p.get('osm_id')} "
                f"name={p.get('name')!r} class={p.get('classification')}"
            )
        if len(problems) > 200:
            lines.append(f"- … +{len(problems) - 200} more in scratch `out/pack_compare.json`")
    lines.append("")
    lines.append("### Terminal audit samples (`no_road` / `tiny`)")
    lines.append("")
    shown = 0
    for r in sorted(ok_rows, key=lambda x: -((x.get("no_road") or 0) + (x.get("tiny") or 0))):
        terms = r.get("terminals_no_road_tiny") or []
        if not terms:
            continue
        lines.append(f"#### {r['bake_id']} (gen `{r.get('generation')}`)")
        lines.append("")
        for t in terms[:25]:
            lines.append(
                f"- `{t.get('kind')}` name={t.get('name')!r} "
                f"node={t.get('node')} lat={t.get('lat')} lon={t.get('lon')} "
                f"comp_nodes={t.get('comp_nodes')}"
            )
        if len(terms) > 25:
            lines.append(f"- … +{len(terms) - 25} more")
        lines.append("")
        shown += 1
        if shown >= 25:
            break

    # ---- stale / below v9 ----
    lines.append("## 4b. Stale or below v9 packs")
    lines.append("")
    lines.append(
        "Fix with a **targeted single-region bake now** "
        "(`./scripts/run-weekly.sh --region <id>`). Add to weekly only if the "
        "region is not already listed **and** it fits the weekly budget — "
        "never a planet run."
    )
    lines.append("")
    stale = rec.get("stale_or_below_v9") or []
    if not stale:
        lines.append("_All current.json packs report graph_format_version ≥ 9._")
    else:
        lines.append("| bake_id | version | in weekly | action | conf line (if later weekly) |")
        lines.append("|---|---:|---|---|---|")
        for s in stale:
            lines.append(
                f"| {s['bake_id']} | {s.get('graph_format_version')} | "
                f"{'Y' if s.get('in_weekly') else ''} | {s.get('action')} | "
                f"`{s.get('regions_conf_line') or ''}` |"
            )
        lines.append("")
        lines.append("```bash")
        lines.append("cd /media/navi/navi-server && set -a; source data/config.env; set +a")
        for s in stale:
            lines.append(f"./scripts/run-weekly.sh --region {s['bake_id']}")
        lines.append("```")
    lines.append("")

    # ---- 5 no ferries ----
    lines.append("## 5. Regions with no ferries")
    lines.append("")
    no_ferry_regions = sorted(
        r["bake_id"]
        for r in per
        if (r.get("in_current") or r.get("in_weekly"))
        and (r.get("route_ferry") or 0) == 0
        and not r.get("composite")
    )
    lines.append(
        f"{len(no_ferry_regions)} non-composite current/weekly regions have zero assigned "
        "`route=ferry` objects."
    )
    lines.append("")
    for bid in no_ferry_regions[:300]:
        lines.append(f"- `{bid}`")
    if len(no_ferry_regions) > 300:
        lines.append(f"- … +{len(no_ferry_regions) - 300} more")
    lines.append("")

    lines.append("## 6. Rollout (admission + duration; ferry links stay opt-in)")
    lines.append("")
    lines.append(
        "This admission/duration change is a **correctness** fix. Ship **on by default** "
        "in the converter (not behind `NAVI_FERRY_LINKS_REGIONS`). Ferry boarding links "
        "remain a separate opt-in."
    )
    lines.append("")
    lines.append("If on by default (recommended): next Monday weekly convert rewrites car "
                 "topology in every weekly region that contains newly admitted ferries "
                 "(see class-change table in §1b). No live `regions.conf` edit is required.")
    lines.append("")
    lines.append("If someone later gates admission behind the ferry-links opt-in (not recommended):")
    lines.append("")
    lines.append("```bash")
    lines.append("# do not set this for admission; ferry-links only")
    lines.append("NAVI_FERRY_LINKS_REGIONS=europe_norway_vestlandet,europe_norway_nord_norge")
    lines.append("# optional global ferry-links flag (separate PR)")
    lines.append("# NAVI_BAKE_FERRY_LINKS=1")
    lines.append("```")
    lines.append("")
    lines.append("Exact weekly `regions.conf` lines for Norway landsdeler (already present; do not edit live):")
    lines.append("")
    lines.append("```")
    lines.append("europe_norway_vestlandet	geofabrik:europe/norway/vestlandet")
    lines.append("europe_norway_trondelag	geofabrik:europe/norway/trondelag")
    lines.append("europe_norway_nord_norge	geofabrik:europe/norway/nord-norge")
    lines.append("europe_norway_sorlandet	geofabrik:europe/norway/sorlandet")
    lines.append("europe_norway_ostlandet	geofabrik:europe/norway/ostlandet")
    lines.append("```")
    lines.append("")

    lines.append("## Appendix: ferries in no region")
    lines.append("")
    lines.append(f"Count: {len(no_region)}. Sample:")
    lines.append("")
    for r in no_region[:40]:
        lines.append(
            f"- `{r.get('key')}` class={r.get('classification')} name={r.get('name')!r}"
        )
    if len(no_region) > 40:
        lines.append(f"- … +{len(no_region) - 40} more")
    lines.append("")

    lines.append("## Reproduction")
    lines.append("")
    lines.append("```bash")
    lines.append("SCRATCH=/tmp/navi-ferry-coverage-YYYYMMDD")
    lines.append("CLONE=/tmp/navi-server-ferry-coverage")
    lines.append("python3 $CLONE/scripts/ferry_coverage/overpass_fetch.py --scratch $SCRATCH")
    lines.append("python3 $CLONE/scripts/ferry_coverage/tag_inventory.py --scratch $SCRATCH")
    lines.append("python3 $CLONE/scripts/ferry_coverage/map_regions.py --scratch $SCRATCH")
    lines.append(
        "python3 $CLONE/scripts/ferry_coverage/enrich_classifier.py --scratch $SCRATCH"
    )
    lines.append("export CARGO_TARGET_DIR=$SCRATCH/target")
    lines.append("cargo build -p pack-convert-core --release --bin ferry_pack_scan")
    lines.append(
        "nohup nice -n 19 ionice -c3 python3 $CLONE/scripts/ferry_coverage/compare_packs.py "
        "--scratch $SCRATCH --scan-bin $SCRATCH/target/release/ferry_pack_scan "
        "> $SCRATCH/logs/pack_compare.log 2>&1 &"
    )
    lines.append("tail -f $SCRATCH/logs/pack_scan_progress.log")
    lines.append(
        "python3 $CLONE/scripts/ferry_coverage/recommend_weekly.py --scratch $SCRATCH"
    )
    lines.append(
        "python3 $CLONE/scripts/ferry_coverage/write_report.py --scratch $SCRATCH --repo $CLONE"
    )
    lines.append("```")
    lines.append("")

    out = repo / "docs" / "ferry-coverage.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}", flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--repo", type=Path, default=Path("/tmp/navi-server-ferry-coverage"))
    ap.add_argument("--live-data", type=Path, default=Path("/media/navi/navi-server/data"))
    args = ap.parse_args()
    run(args.scratch, args.repo, args.live_data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
