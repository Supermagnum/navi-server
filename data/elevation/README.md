# DEM / elevation cache (NAVI_ELEV_DIR)

This directory is the server pack pipeline’s elevation tree. Layout matches
Navi’s `ElevationCache` (`core/src/routing/elevation/cache.rs`):

```text
elevation/
  copernicus/<tile>/…   # or <tile>.hgt
  viewfinder/<tile>.hgt
  srtm/<tile>.hgt
```

`NAVI_ELEV_DIR` in `data/config.env` (and `scripts/config.example.env`) points
here. With `NAVI_BAKE_DELTA_H=1` (default), `navi-indexed-convert --elev-dir`
samples these tiles into graph-pack `edge_delta_h_m`.

Tiles are **not** committed (see repo `.gitignore`). Populate via
`scripts/prefetch-dem-bbox.py` (Copernicus), Navi’s elevation downloader /
integration fixtures, or copy an existing cache into this tree.

## Missing tiles / samples

Copernicus GLO-30 does not publish ocean-only 1° cells (HTTP 404 on prefetch).
An empty tree or a missing tile still allows convert with Δh enabled.

On disk in the graph pack:

| Case | `edge_delta_h_m[i]` |
|------|---------------------|
| Both endpoints sampled | finite metres (`elev(end) − elev(start)`), including `0.0` for a true flat edge |
| Either endpoint missing / void | **`NaN`** (`DELTA_H_MISSING`) |

Do not treat `NaN` as zero climb. Manifest field `delta_h_missing_edges`
counts those sentinel entries across written packs (omitted when zero).
