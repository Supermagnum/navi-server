# Planet / generate mode (test-only)

**Production path is `NAVI_MWM_MODE=comaps-release`** — see
[mwm-pipeline.md](mwm-pipeline.md). This page covers the optional
**generator** path (`NAVI_MWM_MODE=generate`) used only for Nordic/mapgen
experiments. Do not point clients at generate-mode catalogs (v1).

Do **not** run a full CoMaps generator planet bake on the Nordic media host
unless disk/RAM budgets are re-checked. Prefer comaps-release world sidecars
(scratch) for coverage.

## Leaf set (generate)

`config/mwm/leafsets/planet.txt` lists CoMaps `data/borders/*.poly` stems at
the pinned tag (~1159 when generated from `v2026.11.07-16`).

comaps-release world runs use **all** leaves from verified `countries.txt`
(ordered Nordic → Europe → world), not this file.

## Profile (generate)

`config/mwm/profiles/planet.json`: `NODE_STORAGE=mem`, auto threads.  
Nordic generate: `profiles/nordic.json` (`NODE_STORAGE=map`, capped threads).

## Command (test-only, mapgen-class machine)

```bash
export NAVI_MWM_ENABLED=1
export NAVI_MWM_MODE=generate
export NAVI_MWM_LEAFSET=$PWD/config/mwm/leafsets/planet.txt
export NAVI_MWM_PLANET_PBF=/path/to/planet-latest.osm.pbf
# HGT path is generate-mode only; comaps-release uses Terrarium tile cache.
export NAVI_MWM_MAPTERHORN_PMTILES=/path/to/mapterhorn-planet.pmtiles
export NAVI_MWM_COMAPS_ROOT=/var/lib/navi/mwm/comaps
export NAVI_MWM_BUILD_DIR=/var/lib/navi/mwm/omim-build-release
export NAVI_MWM_MAPS_BUILD=/var/lib/navi/mwm/maps_build
./scripts/mwm/build-generator.sh
./scripts/mwm/run-generator.sh
```

## Expected resources (generator planet)

| Metric | Expected |
|--------|----------|
| Wall time | ~33 hours on mapgen-fi-1 (48c / 256 GiB RAM) |
| Disk for full mapgen run | ~650 GiB free |
| Mapterhorn planet 1″ raw HGT | ~1.25 TiB |
| `NODE_STORAGE=mem` | ~100 GiB class |

media (~92 GiB RAM, ~130 GiB free on `Mypool/navi`): insufficient for generator
planet intermediates. Use **comaps-release** streaming sidecars instead.
