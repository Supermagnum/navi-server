# Weekly DEM + Δh missing-edge guards

## Incident (2026-10-01)

`run-planet-leaves-batched.sh` prefetched Copernicus tiles then ran
`prefetch-dem-bbox.py --evict` after each region. The v9 planet-leaves run
ended `2026-10-01T18:16:24Z` having removed ~18 769 DEM tiles from
`data/elevation`. The weekly job had no DEM prefetch of its own and relied
on that shared tree. With `NAVI_BAKE_DELTA_H=1` and an empty elev tree,
convert still set `has_delta_h=true` with millions of `delta_h_missing_edges`.

- No ZFS snapshots on `Mypool/navi`.
- Timer disabled until guards + weekly-owned DEM are in place.

## Policy (approved)

| Tree | Path | Evict |
|------|------|-------|
| Weekly standing cache | `data/elevation` (`NAVI_ELEV_DIR`) | **never** |
| Planet-leaves / smoke | `data/elevation-planet` (`NAVI_ELEV_PLANET_DIR`) | `--evict` OK |

### Road-cell prefetch (not bbox)

1. `build-dem-cell-list.py` scans the region PBF with pyosmium (`locations=True`)
   and collects `floor(lat),floor(lon)` for **every node** on ways with
   `highway=*` or ferry tags — including intermediate shape points.
2. Cache: `data/state/dem_cells/<region_id>.json` keyed by PBF size+mtime_ns.
3. `prefetch-dem-bbox.py --cells-file` downloads only those cells into
   `NAVI_ELEV_DIR`. Existing non-empty `.tif` files are skipped (Norway’s 222
   tiles stay).
4. Ocean/404 stems go to `elev_dir/copernicus_ocean_404.txt` (per-source index
   with `# source=copernicus` header). Refresh:
   - `--refresh-404` with the same cells/bbox (drop overlapping stems)
   - `--refresh-404-all` (clear index)

### Size estimates → measured

Pre-fill road-cell union estimate (~33 GiB, 1851 cells) used Norway on-disk
medians + cos(lat) for tropics. **After the first real fill**, replace the
tables below with `du` / per-tile measured sizes (especially Mexico and other
tropical regions).

#### Pre-fill estimate (replace after first real fill)

| Scope | Cells | Est. GiB |
|-------|------:|---------:|
| Graph-node union (38 weekly, incl. Antarctica) | 1851 | ~27–33 |
| PBF-scaled union (median ratio 1.09 on held extracts) | ~2030 | ~27–35 |
| Antarctica alone (graph = PBF proxy) | 90 | ~0.5–1 |
| Already on disk (Norway Copernicus `.tif`) | 222 | 3.4 |
| Remaining download (approx.) | ~1800 | ~24–32 |

Tile-size notes: Norway median ~19 MiB/tile, mean ~15 MiB (terrain voids
shrink some cells). Tropical land tiles are often larger — **record `du`
per region after the first fill** and replace this table.

#### PBF shape-point cells vs graph-node estimate

Measured on held extracts (highway+ferry way nodes including **all shape
points** vs published car+foot graph nodes):

| region | graph cells | PBF cells | Δ | PBF/graph |
|--------|------------:|----------:|--:|----------:|
| europe_norway_vestlandet | 27 | 32 | +5 | 1.19 |
| europe_norway_sorlandet | 9 | 18 | +9 | 2.00 |
| hedmark | 13 | 13 | 0 | 1.00 |
| us_west_virginia | 16 | 16 | 0 | 1.00 |

PBF is always ≥ graph here (`only_graph=0`). Sorlandet doubles because coastal
ferries/highways cross cells without graph junctions. Prefetch and coverage
use the PBF list. Median PBF/graph on held extracts ≈ 1.09; coastal
outliers up to 2.0.

### Incomplete DEM

Convert skips the region when any road-cell is neither on disk nor in the
404 index; previous published generation stays live; weekly continues; logs
name the skip. Prefer `delta_h=0` on `regions.conf` over permanent skip when
a region cannot be covered at all.

## Guard rules

### Convert

When Δh is enabled for the region (`NAVI_BAKE_DELTA_H=1` and not
`delta_h=0` on the conf line):

1. Prefetch road-cells into `NAVI_ELEV_DIR` (no `--evict`).
2. Require **every** road-cell present or known-404.
3. On miss: soft-skip region; continue.

Per-region: `delta_h=0` (or `false`/`off`) bakes without elevation
(`has_delta_h=false`). Client/core already gates climb/eco on that flag.

### Validate / publish

- Regression vs latest published: `missing > max(prev×10, prev+50k)` fails
  that region only.
- First gen: share `0.05` if `edge_count` present, else abs `50000`.
- Publish omits failed/missing converts; previous gens stay live.

## Re-enable checklist

- [ ] Guard + weekly DEM supply merged and installed into live `scripts/` +
      `data/regions.conf.bboxes.json`
- [ ] `data/elevation-planet` created; planet runners point there
- [ ] Weekly DEM fill complete; Norway tiles retained; measured sizes recorded
- [ ] Acceptance scratch converts (Norway×5, Mexico, Antarctica) within guard
      of published baselines
- [ ] Then: `systemctl enable --now navi-pack-bake.timer`

## Ops

```bash
# Fill standing cache (as navit-server; after fetch-extracts for weekly regions)
./scripts/fill-weekly-dem.sh

# Refresh 404 index for one region’s cells
./scripts/prefetch-dem-bbox.py --elev-dir "$NAVI_ELEV_DIR" \
  --cells-file "$NAVI_STATE_DIR/dem_cells/RID.json" --refresh-404

# Clear entire 404 index (occasional full refresh)
./scripts/prefetch-dem-bbox.py --elev-dir "$NAVI_ELEV_DIR" --refresh-404-all
```

### Fill expectations (pre-measured-size)

| | |
|--|--|
| Weekly regions | 38 (incl. Antarctica; 4 skip_reason excluded) |
| Expected union cells | ~2000–2200 (PBF shape nodes) |
| Keep | 222 Norway `.tif` already in `data/elevation` (~3.4 GiB) |
| New download | ~1800 cells, ~24–32 GiB (tropics may push upper end) |
| Wall time | fetch extracts (hours if cold) + cell-list scan ~2–5 h + DEM DL ~1–3 h |
| Never | `--evict` on `NAVI_ELEV_DIR`; do not touch timer yet |

## Tests

```bash
./scripts/test-dem-coverage.py
./scripts/test-delta-h-missing-guard.sh
./scripts/test-dem-ocean-skip.py
```
