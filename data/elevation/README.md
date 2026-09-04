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

Tiles are **not** committed (see repo `.gitignore`). Populate via Navi’s
elevation downloader / integration fixtures, or copy an existing cache into
this tree. An empty tree still allows convert with Δh enabled; missing tiles
simply yield no elevation sample for those edges.
