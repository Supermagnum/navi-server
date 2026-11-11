# MWM pipeline config (Nordic pilot)

Off by default: set `NAVI_MWM_ENABLED=1` only after reviewing
`docs/client-fetch-mwm.md` and running `/tmp/navi-mwm-apply.sh` (operator).

## Pin

`comaps-pin.json` pins CoMaps tag + SHA. `scripts/mwm/build-generator.sh`
fetches and builds **outside** this repo (default
`$NAVI_MWM_COMAPS_ROOT`, e.g. `/var/lib/navi/mwm/comaps`).

Build targets: `generator_tool`, `routes_builder_tool` only.

## Leaf sets

| File | Purpose |
|------|---------|
| `leafsets/nordic.txt` | Pilot: NO+SE+DK+FI + Svalbard/Jan Mayen (excludes Bouvet) |
| `leafsets/planet.txt` | Prepare-only full CoMaps borders list; do not run on media |

Every generator run regenerates a matching `countries.txt` / borders overlay
from the leaf set and validates: every leaf has a border, every in-set
neighbour needed for `cross_mwm` is present, and no leftover borders from
another leaf set remain in the working overlay (spike 3 crash).

## Input PBF (Nordic)

Default: a **Nordic merge** of Geofabrik extracts covering Norway, Sweden,
Denmark, and Finland (including Svalbard/Jan Mayen coverage in the Norway
extract). Documented env: `NAVI_MWM_PLANET_PBF`.

Scratch reference used in the first e2e:
`…/inputs/scandinavia-norse.osm.pbf` (bbox covers Norden including Denmark).

## Limitation (pilot)

Cross-border routing **out of** the Nordic leaf set (e.g. into Germany,
Russia, Baltic states) is **not** supported: external neighbours are not
baked, so `cross_mwm` connectors stop at the Nordic frontier.

## Never mix builds

One `{version}/` release = one generator run. Never copy MWMs from another
run into the same version directory.
