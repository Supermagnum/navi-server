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

Tiles are **not** committed (see repo `.gitignore`). Populate via
`scripts/prefetch-dem-bbox.py` (Copernicus) or copy an existing DEM cache into
this tree. Convert never downloads DEM over the network.


## Missing tiles / samples

Copernicus GLO-30 does not publish ocean-only 1° cells (HTTP 404 on prefetch).
`scripts/prefetch-dem-bbox.py` skips cells that do not intersect the extract
Osmosis `.poly` (soft-fetched by `fetch-extracts.sh` beside the PBF) and
records confirmed 404 stems in `elevation/copernicus_ocean_404.txt`
(persistent negative cache; gitignored; not under scratch/). Missing or
unusable `.poly` fails open (full bbox grid). Unit tests:
`scripts/test-dem-ocean-skip.py`. An empty tree or a missing tile still
allows convert with Δh enabled.

On disk in the graph pack:

| Case | `edge_delta_h_m[i]` |
|------|---------------------|
| Both endpoints sampled | finite metres (`elev(end) − elev(start)`), including `0.0` for a true flat edge |
| Either endpoint missing / void | **`NaN`** (`DELTA_H_MISSING`) |

Do not treat `NaN` as zero climb. Manifest field `delta_h_missing_edges`
counts those sentinel entries across written packs (omitted when zero).
