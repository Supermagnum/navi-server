# Region mapping — Navi `.rkyv` ↔ CoMaps leaves

Builds the **Navi region → CoMaps leaf** migration table and the
**country-grouped leaf list** used by the MWM catalog / download UI.

Related docs:

- [mwm-pipeline.md](mwm-pipeline.md) — operator bake / publish / timers
- [client-fetch-mwm.md](client-fetch-mwm.md) — catalog v2 fields clients consume
- [client-migration-from-rkyv.md](client-migration-from-rkyv.md) — app migration plan

On media, `.rkyv` packs are **retired**; elsewhere they may still be frozen and
served until a deliberate retirement step. This tooling only adds catalog /
migration JSON under `published/mwm/`; it does **not** rewrite sidecars or
touch `published/packs/`.

---

## Purpose

| Artifact role | Consumer |
|---------------|----------|
| `country_groups` / `country_groups.json` | Primary **region download UI** (CoMaps `countries.txt` hierarchy) |
| `region_to_leaves` / `region-migration.json` | **Migration only** — users who already have a Navi `.rkyv` pack |
| `display_name_overrides` | Current display names where CoMaps leaf labels lag admin reforms |

Do not mix UI and migration roles. See
[client-fetch-mwm.md §5](client-fetch-mwm.md#5-region-ui-country_groups-vs-rkyv-migration-region_to_leaves).

---

## Generator: `scripts/mwm/build-region-mapping.py`

Read-only against Geofabrik `.poly` extracts and CoMaps `data/borders`.
Optional soft-fetch of missing region `.poly` files into `--poly-cache` only
(never writes into extracts or borders trees).

### Inputs

| Arg / source | Meaning |
|--------------|---------|
| `--regions-json` | Published `.rkyv` `current.json` (region list) |
| `--extracts-dir` | Directory of Geofabrik-style `.poly` files |
| `--borders-dir` | CoMaps `data/borders` (leaf polygons) |
| `--countries-txt` | CoMaps `countries.txt` (hierarchy + sizes) |
| `--overrides` | `config/mwm/catalog/display_name_overrides.json` (default) |
| `--catalog` | Optional existing MWM catalog for sidecar byte sizes |
| `--prev-migration` | Previous `region-migration.json` for diff output |

### Outputs (under `--out-dir`)

| File | Schema / role |
|------|----------------|
| `region-migration.json` | `navi-mwm-region-migration/v1` — full overlap detail |
| `country_groups.json` | `navi-mwm-country-groups/v1` — UI tree (`countries` array) |
| `region_leaf_mapping.json` | Compact map for `publish-mwm.sh` (`--region-map-out`) |
| `region-migration-diff.json` | Diff vs `--prev-migration` when provided |

### Thresholds (defaults)

| Knob | Default | Meaning |
|------|---------|---------|
| `--include-threshold-pct` | **0.5%** of region area | Leaf must cover at least this much of the Navi region |
| `--micro-threshold-pct` | **0.05%** | Keep stable land-only overlaps as `micro_land_overlap` (border roads / places) |
| `--mostly-outside-min-coverage-pct` | **25%** | If ≥50% of the leaf lies outside the region, keep it only when it still covers ≥ this share of the region (drops neighbouring-country coastal noise) |

CoMaps borders are land polygons (no ocean). Gaps on coastal / island regions
are often ocean or fjord in Geofabrik polys, not missing leaves. The same
sea/fjord geometry can create thin intersections with neighbouring-country
land borders; those hits are dropped unless the leaf covers ≥25% of the Navi
region (`DEFAULT_MOSTLY_OUTSIDE_MIN_COVERAGE_PCT`). Library defaults live in
`scripts/mwm/lib/region_mapping.py`.

To repair an already-published migration without re-running polygon overlap:

```bash
python3 scripts/mwm/repair-region-mapping-mostly-outside.py \
  --migration /media/navi/navi-server/data/published/mwm/261105/region-migration.json \
  --out-dir config/mwm/catalog \
  --catalog /media/navi/navi-server/data/published/mwm/current.json
sudo bash scripts/mwm/apply-region-mapping-mostly-outside-fix.sh
```

### Example (hand-run)

```bash
python3 scripts/mwm/build-region-mapping.py \
  --regions-json /media/navi/navi-server/data/published/current.json \
  --extracts-dir /media/navi/navi-server/data/scratch/extracts \
  --borders-dir /path/to/comaps/data/borders \
  --countries-txt /path/to/comaps/data/countries.txt \
  --out-dir /tmp/region-mapping-out \
  --catalog /media/navi/navi-server/data/published/mwm/261105/catalog.json \
  --comaps-release 261105
```

Committed catalog copies used for review / live publish live under
`config/mwm/catalog/`.

---

## Pipeline wiring

When all three env vars are set, `scripts/mwm/run-comaps-release-bake.sh` runs
the generator during a CoMaps-release bake; `scripts/mwm/publish-mwm.sh`
attaches the artifacts to the release catalog.

| Variable | Role |
|----------|------|
| `NAVI_MWM_BORDERS_DIR` | CoMaps `data/borders` (read-only; required to enable the step) |
| `NAVI_MWM_EXTRACTS_DIR` | Geofabrik `.poly` extracts (defaults from `NAVI_EXTRACTS_DIR` if set) |
| `NAVI_MWM_REGIONS_JSON` | Published `.rkyv` `current.json` region list |
| `NAVI_MWM_REGION_MAP` | Compact map path (default `config/mwm/catalog/region_leaf_mapping.json`) |
| `NAVI_MWM_DISPLAY_OVERRIDES` | Display-name overrides JSON |

If any of the three enabling vars is missing, the bake logs a WARN and skips
mapping (sidecars still publish). Full key table:
[mwm-pipeline.md — Config keys](mwm-pipeline.md#config-keys-env).

---

## Catalog artifacts

Committed / generated under `config/mwm/catalog/`:

| Path | Role |
|------|------|
| `region-migration.json` | Full overlap detail for migrators |
| `region_leaf_mapping.json` | Compact `navi_region_to_comaps_leaves` + `shared_leaves` |
| `country_groups.json` | Country-grouped UI list |
| `display_name_overrides.json` | Reviewable leaf → display name map |

Published on the live MWM tree (release dir + catalog pointer):

| Live path | Notes |
|-----------|--------|
| `published/mwm/<ver>/region-migration.json` | Same payload as config copy |
| `published/mwm/<ver>/country_groups.json` | Same payload as config copy |
| `published/mwm/<ver>/catalog.json` | Catalog v2 with mapping fields |
| `published/mwm/current.json` | Pointer = catalog (written **last**) |

Catalog fields filled by publish:

| Field | Meaning |
|-------|---------|
| `region_to_leaves` | bake_id → leaf id list |
| `shared_leaves` | leaf → bake_ids that share it |
| `country_groups` | Inline UI tree |
| `country_groups_url` | `{ver}/country_groups.json` |
| `region_migration_url` | `{ver}/region-migration.json` |
| `display_name_overrides` | `leaves` + `likely_outdated_elsewhere` |

Leaf entries may also get `shared` / `shared_with_regions` refreshed from the
compact map. Sidecar byte fields are left alone.

---

## Live publish: `/tmp/navi-region-mapping-publish.sh`

Host operator script (not installed by systemd). **Requires root** for a ZFS
snapshot and writes under the live Apache DocumentRoot MWM tree.

Catalog / mapping only — **no** sidecar or `.rkyv` changes.

### Prerequisites

- Live MWM release present: `/media/navi/navi-server/data/published/mwm/261105/`
- Source artifacts in the scratch checkout (or override `SRC_DIR`):
  - `config/mwm/catalog/region-migration.json`
  - `config/mwm/catalog/country_groups.json`
  - `config/mwm/catalog/region_leaf_mapping.json`
  - `config/mwm/catalog/display_name_overrides.json` (optional but expected)
- `zfs` available; dataset that backs `/media/navi` must exist
- Outbound HTTPS to `https://navigate-me.duckdns.org/` for post-publish verify

### Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `LIVE_MWM` | `/media/navi/navi-server/data/published/mwm` | Live MWM publish root |
| `VER` | `261105` | Release directory name |
| `SRC_DIR` | scratch `config/mwm/catalog` | Source JSON directory |
| `REPO` | scratch navi-server checkout | For display-name overrides path |
| `ZFS_DATASET` | **auto-detect** from `findmnt` on `LIVE_MWM` / `/media/navi` | Dataset to snapshot (on this host: **`Mypool/navi`**) |
| `ZFS_POOL` | (legacy alias of `ZFS_DATASET`) | Prefer `ZFS_DATASET` |
| `BASE_URL` | `https://navigate-me.duckdns.org` | HTTP verify base |

Auto-detect uses `findmnt -n -o SOURCE --target …` and checks the name with
`zfs list`. It falls back to `Mypool/navi` when that dataset exists. Do **not**
set `ZFS_POOL=navi` — that is a pool/dataset name that does not exist on this
host (the earlier failure mode).

### What it does

1. Create ZFS snapshot `${ZFS_DATASET}@navi-region-mapping-<UTC>`
2. Stage merged catalog + copy migration / country_groups
3. File rollback copy under `published/mwm/.rollback-region-mapping-<ver>/`
4. Atomic install order: `region-migration.json` → `country_groups.json` →
   `catalog.json` → **`current.json` last**
5. HTTP verify: `current.json`, `region-migration.json`, `country_groups.json`
   (schema + Østlandet spot checks)

### Usage

```bash
# Review first (script is root-only; detection is safe to reason about via findmnt):
findmnt -n -o SOURCE --target /media/navi/navi-server/data/published/mwm
# expect: Mypool/navi

sudo /tmp/navi-region-mapping-publish.sh

# Optional explicit dataset:
sudo ZFS_DATASET=Mypool/navi /tmp/navi-region-mapping-publish.sh
```

Snapshot name is written to `/tmp/navi-region-mapping-snapshot.txt`.
Rollback directory path is written to `/tmp/navi-region-mapping-rollback-dir.txt`.

### Rollback

Prefer **file** rollback (MWM catalog only):

```bash
ROLLBACK=$(cat /tmp/navi-region-mapping-rollback-dir.txt)
sudo cp -a "$ROLLBACK/catalog.json" \
  /media/navi/navi-server/data/published/mwm/261105/catalog.json
sudo cp -a "$ROLLBACK/current.json" \
  /media/navi/navi-server/data/published/mwm/current.json
# optional: restore region-migration.json / country_groups.json from $ROLLBACK
```

ZFS rollback of the whole dataset is last resort and disruptive:

```bash
SNAP=$(cat /tmp/navi-region-mapping-snapshot.txt)
# WARNING: rolls back entire Mypool/navi — prefer file rollback above
sudo zfs rollback "$SNAP"
```

---

## `.rkyv` status reminder

On **media**, `.rkyv` packs are **retired**: `data/published/packs/` is empty,
root `current.json` is a retired stub, and `.navi-rkyv-frozen` records
permanent disablement (packs snapshot on `navi-server-backup`). Weekly bake /
scrub stay disabled. Hosts that still serve frozen packs must not delete them
until the Navi client has switched to MWM and retirement is explicitly
approved. See [client-migration-from-rkyv.md](client-migration-from-rkyv.md)
and [mwm-pipeline.md](mwm-pipeline.md).
