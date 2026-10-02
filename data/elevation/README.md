# DEM / elevation cache (NAVI_ELEV_DIR)

This directory is the server pack pipeline’s elevation tree. Layout matches
`pack_convert_core::routing::elevation::ElevationCache`
(`pack-convert-core/src/routing/elevation/cache.rs`):

```text
elevation/
  copernicus/<tile>/…   # or <tile>.hgt
  viewfinder/<tile>.hgt
  srtm/<tile>.hgt
```

`NAVI_ELEV_DIR` in `data/config.env` (and `scripts/config.example.env`) points
here. With `NAVI_BAKE_DELTA_H=1` (default), `navi-indexed-convert --elev-dir`
samples these tiles into graph-pack `edge_delta_h_m`.

Tiles are **not** committed (see repo `.gitignore`). Weekly standing cache:
`scripts/fill-weekly-dem.sh` / convert prefetch via
`scripts/prefetch-dem-bbox.py --cells-file` (Copernicus road-cells). Planet
runners use `data/elevation-planet` (`NAVI_ELEV_PLANET_DIR`) and may
`--evict`. Convert never downloads DEM itself — scripts do before convert.


## Road-cell coverage (weekly)

Cell lists come from every node on `highway=*` and ferry ways in the region
PBF (including intermediate shape points), cached under
`data/state/dem_cells/<region_id>.json`. Convert requires each cell present
or listed in the 404 index; otherwise the region is soft-skipped. Per-region
`delta_h=0` on `regions.conf` disables Δh without DEM. See
`docs/dem-weekly.md`.


## Missing tiles / samples

Copernicus GLO-30 does not publish ocean-only 1° cells (HTTP 404 on prefetch).
`scripts/prefetch-dem-bbox.py` records confirmed 404 stems in
`elevation/copernicus_ocean_404.txt` (per-source index; gitignored). Refresh:
`--refresh-404` (drop stems overlapping the request) or `--refresh-404-all`.
Legacy bbox mode may also skip cells outside the extract `.poly`. Unit tests:
`scripts/test-dem-ocean-skip.py`.

On disk in the graph pack:

| Case | `edge_delta_h_m[i]` |
|------|---------------------|
| Both endpoints sampled | finite metres (`elev(end) − elev(start)`), including `0.0` for a true flat edge |
| Either endpoint missing / void | **`NaN`** (`DELTA_H_MISSING`) |

Do not treat `NaN` as zero climb. Manifest field `delta_h_missing_edges`
counts those sentinel entries across written packs (omitted when zero).
