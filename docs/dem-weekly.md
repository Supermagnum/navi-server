# Weekly DEM + Δh missing-edge guards

## Incident (2026-10-01)

`run-planet-leaves-batched.sh` prefetches Copernicus tiles then runs
`prefetch-dem-bbox.py --evict` after each region. The v9 planet-leaves run
ended `2026-10-01T18:16:24Z` having removed ~18 769 DEM tiles from
`data/elevation`. The weekly job (`navi-pack-bake.timer` → `run-weekly.sh`)
had **no DEM prefetch of its own** and relied on that shared tree. With
`NAVI_BAKE_DELTA_H=1` and an empty elev tree, `navi-indexed-convert` still
sets `has_delta_h=true` and records millions of `delta_h_missing_edges`
(NaN Δh), which would have been published.

- No ZFS snapshots on `Mypool/navi` — nothing to restore.
- Timer disabled until guards + weekly-owned DEM are in place.
- Norway five were re-fetched by hand (no `--evict`); other weekly regions
  still need DEM via the weekly-owned supply (follow-up).

## Guard rules (this change)

### Convert (`scripts/convert-region.sh`)

When `NAVI_BAKE_DELTA_H=1`:

1. Require `NAVI_ELEV_DIR`.
2. Before convert, call `lib/dem_coverage.region_dem_ok`:
   - If `${NAVI_REGIONS_CONF}.bboxes.json` has the region: require **≥1**
     DEM cell present for that bbox grid under `elev_dir/copernicus/…`
     (or loose `*.tif`/`*.hgt` matching the stem).
   - If no bbox entry: require **any** DEM payload under `elev_dir` (legacy
     fallback; validate still catches empty-Δh regressions).
3. On coverage miss: **skip** that region (log `reason=dem_coverage`), remove
   any partial convert dir, **continue** with other regions. Previous
   published generation stays live.
4. Hard convert failures also skip that region and continue. The convert step
   exits 0 after processing all matched regions so weekly does not abort.

### Validate (`scripts/validate-packs.sh`)

When `has_delta_h` is true:

- **With previous published `*.navi-manifest.json`:** fail the region if
  `delta_h_missing_edges > max(prev × NAVI_DELTA_H_MISSING_MAX_FACTOR,
  prev + NAVI_DELTA_H_MISSING_ABS_FLOOR)`
  (defaults: factor `10`, floor `+50000`).
- **First generation (no baseline):**
  - If manifest has `edge_count` / `edges`: fail when
    `missing/edges > NAVI_DELTA_H_MISSING_FIRST_GEN_SHARE` (default `0.05`).
  - Else: fail when `missing >= NAVI_DELTA_H_MISSING_FIRST_GEN_ABS`
    (default `50000`). Coarse empty-DEM gate; convert coverage is primary
    for tiny extracts.

### Publish (`scripts/publish-packs.sh`)

- Skip assemble when convert output is missing (previous gen stays live).
- Validate **per region**; on failure, omit that region from staging and
  continue publishing the rest. If every staged region fails validate, exit
  0 without touching the live tree.

## Weekly DEM policy

Tracked in the follow-up (standing cache vs fetch+evict, planet-leaves
isolation, fill remaining weekly regions). See the checklist below before
re-enabling `navi-pack-bake.timer`.

## Re-enable checklist

- [ ] This guard merged and installed into the live `scripts/` tree from `main`
- [ ] Weekly flow owns DEM (prefetch before convert; no shared `--evict` wipe)
- [ ] Every non-skipped weekly region has DEM coverage **or** is safely skipped
      by the convert/validate guards
- [ ] Scratch dry-run: one Norway + one non-Norway region; empty-elev validate
      fails and leaves previous gen live
- [ ] Then: `systemctl enable --now navi-pack-bake.timer` (operator only)

## Tests

```bash
./scripts/test-dem-coverage.py
./scripts/test-delta-h-missing-guard.sh
```
