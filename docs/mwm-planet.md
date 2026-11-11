# Planet MWM test (prepare only)

Do **not** run a planet bake on the Nordic media host.

## Leaf set

`config/mwm/leafsets/planet.txt` lists every CoMaps `data/borders/*.poly` stem
at the pinned tag (1159 leaves when generated from `v2026.11.07-16`).

## Command (later, on a mapgen-class machine)

```bash
export NAVI_MWM_ENABLED=1
export NAVI_MWM_LEAFSET=$PWD/config/mwm/leafsets/planet.txt
export NAVI_MWM_PLANET_PBF=/path/to/planet-latest.osm.pbf
export NAVI_MWM_MAPTERHORN_PMTILES=/path/to/mapterhorn-planet.pmtiles
export NAVI_MWM_COMAPS_ROOT=/var/lib/navi/mwm/comaps
export NAVI_MWM_BUILD_DIR=/var/lib/navi/mwm/omim-build-release
export NAVI_MWM_MAPS_BUILD=/var/lib/navi/mwm/maps_build
export NAVI_MWM_NODE_STORAGE=mem          # CoMaps guidance ~100GB RAM class
export NAVI_MWM_THREADS_FEATURES=0        # auto
export NAVI_MWM_MEMORY_MAX=256G           # example
./scripts/mwm/build-generator.sh
./scripts/mwm/run-generator.sh
```

## Expected resources (from spike 3 / CoMaps production notes)

| Metric | Expected |
|--------|----------|
| Wall time | ~33 hours (CoMaps mapgen-fi-1 class) |
| Disk for full mapgen run | ~650 GiB |
| Mapterhorn planet 1″ raw HGT | ~1.25 TiB |
| Cadence (upstream maps) | Roughly weekly |
| Cadence (Navi pilot recommendation) | Biweekly Nordic on media; planet on mapgen |

Sources: `/tmp/navi-mwm-spike3.md` part 1; CoMaps wiki Production-maps-generation.
