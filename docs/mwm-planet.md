# Planet MWM test (prepare only)

Do **not** run a planet bake on the Nordic media host. See `/tmp/navi-mwm-ram.md`
for the measured Nordic scaling and the media go/no-go.

## Leaf set

`config/mwm/leafsets/planet.txt` lists every CoMaps `data/borders/*.poly` stem
at the pinned tag (1159 leaves when generated from `v2026.11.07-16`).

## Profile

`config/mwm/profiles/planet.json` selects CoMaps production-style settings:
`NODE_STORAGE=mem`, auto threads (`0`). Nordic uses `profiles/nordic.json`
(`NODE_STORAGE=map`, capped threads) for low-RAM hosts.

## Command (later, on a mapgen-class machine)

```bash
export NAVI_MWM_ENABLED=1
export NAVI_MWM_LEAFSET=$PWD/config/mwm/leafsets/planet.txt
export NAVI_MWM_PLANET_PBF=/path/to/planet-latest.osm.pbf
export NAVI_MWM_MAPTERHORN_PMTILES=/path/to/mapterhorn-planet.pmtiles
export NAVI_MWM_COMAPS_ROOT=/var/lib/navi/mwm/comaps
export NAVI_MWM_BUILD_DIR=/var/lib/navi/mwm/omim-build-release
export NAVI_MWM_MAPS_BUILD=/var/lib/navi/mwm/maps_build
# Profile planet.json applies unless NAVI_MWM_NO_PROFILE=1:
#   NODE_STORAGE=mem, THREADS_*=0 (auto), MEMORY_MAX hint 256G
./scripts/mwm/build-generator.sh
./scripts/mwm/run-generator.sh
```

## Expected resources (CoMaps production + spike 3)

| Metric | Expected |
|--------|----------|
| Wall time | ~33 hours on mapgen-fi-1 (48c / 256 GiB RAM) |
| Disk for full mapgen run | ~650 GiB free required |
| Mapterhorn planet 1″ raw HGT | ~1.25 TiB |
| `NODE_STORAGE=mem` | ~100 GiB class (ini comment, 2025) |
| Cadence (upstream maps) | Roughly weekly |
| Cadence (Navi pilot recommendation) | Biweekly Nordic on media; planet on mapgen |

media today: **92 GiB RAM**, **~130 GiB free** on `Mypool/navi` — insufficient disk
for 650 GiB planet intermediates, and RAM is below CoMaps `mem` guidance.

Sources: `/tmp/navi-mwm-spike3.md`; CoMaps wiki Production-maps-generation /
Infrastructure (mapgen-fi-1).
