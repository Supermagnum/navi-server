# Operator guide — CoMaps-release MWM sidecar pipeline

Scratch-first. Default **`NAVI_MWM_ENABLED=0`**. Live bake stays off until
`/tmp/navi-mwm-apply.sh` (or equivalent) is reviewed.

Does **not** touch live `.rkyv` `published/current.json` or `published/packs/`.
Shares `pack-pipeline.lock` with the `.rkyv` pipeline — never run MWM bake while
that lock is held.

**`.rkyv` status (serving-only freeze):** weekly bake and daily scrub timers are
disabled on the live host; published packs stay served unchanged. MWM schedule
guard pauses around Monday **00:00 UTC** **only while** `navi-pack-bake.timer`
is enabled (or `NAVI_MWM_RKYV_BAKE_TIMER_ENABLED=1`). When the timer is disabled
or `published/.navi-rkyv-frozen` exists, the Monday pause is skipped.

**Do not retire `.rkyv` until the Navi client is tested on MWM.** Freeze ≠
delete. Retirement (stop serving → remove `published/packs/` + old bake code →
free disk) is a separate deliberate step documented in
[client-migration-from-rkyv.md](client-migration-from-rkyv.md#step-8--retire-rkyv-packs-server-later--deliberate).

**Region mapping (each CoMaps release):** when `NAVI_MWM_BORDERS_DIR`,
`NAVI_MWM_EXTRACTS_DIR`, and `NAVI_MWM_REGIONS_JSON` are set, the bake runs
`build-region-mapping.py` (read-only polys) and publish attaches
`region-migration.json`, `country_groups.json`, and catalog fields
`country_groups` / `region_migration_url` / `display_name_overrides`. Diff vs
the previous release’s migration is written as `region-migration-diff.json`.
Operator detail (generator, thresholds, live `/tmp` publish, ZFS dataset):
[region-mapping.md](region-mapping.md).

**Unfreeze (operator):** remove `published/.navi-rkyv-frozen`, then
`systemctl enable --now navi-pack-bake.timer navi-pack-scrub.timer` (or run
`/tmp/navi-rkyv-unfreeze.sh`). Deliberate bake while frozen needs
`NAVI_RKYV_ALLOW_FROZEN=1` / `--allow-frozen`.

---

## Modes

| `NAVI_MWM_MODE` | Purpose |
|-----------------|---------|
| **`comaps-release`** (default) | Discover CoMaps planet release; download leaves; bake sectioned NVSC sidecars; publish sidecars + catalog v2. **Production path.** |
| `generate` | Nordic CoMaps **generator** leaf bake. **Test-only.** Not used for client catalog v2. |

Stale spike designs (generator-first as default, flat spike2 sidecar as the
client format, midpoint PBF bind, HGT conversion for comaps-release) are
**retired**. Elevation for comaps-release samples **Mapterhorn Terrarium tiles**
directly (`build-elevation-overrides.py`); `NAVI_MWM_HGT_CACHE` remains only for
generate-mode / legacy tools.

---

## Config keys (env)

Defaults from `scripts/mwm/lib/common.sh` / `config/mwm/measured-peaks.json`.

| Variable | Default | Meaning |
|----------|---------|---------|
| `NAVI_MWM_ENABLED` | `0` | Must be `1` for timer bake (or pass `--force` scratch) |
| `NAVI_MWM_MODE` | `comaps-release` | See above |
| `NAVI_MWM_PUBLISHED_ROOT` | `{repo}/data/published/mwm` | Sidecar publish root |
| `NAVI_MWM_LOCK` | `{repo}/data/state/pack-pipeline.lock` | Shared flock |
| `NAVI_MWM_LEAFSET` | `config/mwm/leafsets/nordic.txt` | Leaf list (ignored if `ALL_LEAVES=1`) |
| `NAVI_MWM_ALL_LEAVES` | `0` | `1` = world (ordered Nordic→Europe→world) |
| `NAVI_MWM_MAPTERHORN_CACHE` | `{repo}/data/elevation/mapterhorn-cache` | **Persistent** tile cache (survives releases) |
| `NAVI_MWM_MAPTERHORN_CACHE_MAX_BYTES` | `58755104232` (54.72 GiB) | mtime-LRU eviction cap (261105 world measured 45.60 GiB + 20%) |
| `NAVI_MWM_MAPTERHORN_ZOOM` | `12` | Terrarium zoom |
| `NAVI_MWM_ELEV_THRESHOLD_M` | `2` | Override when \|Δ\| exceeds this |
| `NAVI_MWM_PROCESS_CONCURRENCY` | `0` (auto) | `0` → from RAM + per-stage peaks; override with explicit int |
| `NAVI_MWM_PER_LEAF_GIB` | empty | Optional single-value override; empty uses `stage_peaks_gib` |
| `NAVI_MWM_RAM_HEADROOM_GIB` | `16` | Reserved when deriving concurrency |
| `NAVI_MWM_ANNOTATE_CONCURRENCY` | `2` | Semaphore cap on concurrent wetland annotate stages |
| `NAVI_MWM_TILE_FETCH_CONCURRENCY` | `16` | Per-leaf concurrent Mapterhorn HTTP tile fetches |
| `NAVI_MWM_TILE_MEM_CACHE` | `4096` | Decoded RGB tile LRU capacity per worker |
| `NAVI_MWM_PREFETCH_DISK_BUDGET_GIB` | `20` | Disk budget for prefetched MWMs in `tmp/_downloads` |
| `NAVI_MWM_DOWNLOAD_CONCURRENCY` | `2` | Parallel MWM HTTP downloads |
| `NAVI_MWM_SCHEDULE_GUARD` | `1` | Pause around weekly `.rkyv` bake when that timer is active |
| `NAVI_MWM_RKYV_BAKE_TIMER_ENABLED` | (auto) | `0`/`1` override; else frozen marker / `systemctl is-enabled navi-pack-bake.timer` |
| `NAVI_MWM_WEEKLY_BAKE_WEEKDAY` | `0` | UTC weekday of weekly bake (`0`=Mon) |
| `NAVI_MWM_WEEKLY_BAKE_UTC` | `00:00` | UTC `HH:MM` of weekly bake |
| `NAVI_MWM_PAUSE_BEFORE_HOURS` | `6` | Hours before weekly bake to pause starting new leaves |
| `NAVI_MWM_PAUSE_AFTER_HOURS` | `6` | Hours after weekly bake start until pause window ends |
| `NAVI_MWM_MEASURED_PEAKS` | `config/mwm/measured-peaks.json` | Peak RSS source |
| `NAVI_MWM_METASERVER` | `https://cdn-us-1.comaps.app` | CoMaps metaserver |
| `NAVI_MWM_MAP_SERIES` | `2026.10.27` | maps.json series key |
| `NAVI_MWM_COMAPS_VERSION` | empty | Pin data-version; empty = latest |
| `NAVI_MWM_GRACE_DAYS` | `7` | Retain superseded releases |
| `NAVI_MWM_KEEP_RELEASES` | `2` | Retain count floor |
| `NAVI_MWM_HTTP_USER_AGENT` | `navi-server-mwm/1.0 (+…)` | Polite client |
| `NAVI_MWM_SKIP_ROUTE_CHECK` | `0` | `1` skips route validation (offline CI only). Normal path stages leaf MWMs from the CoMaps CDN, runs six gated car routes + foot wetland detour + informational routes, then deletes staged MWMs. |
| `NAVI_MWM_GEOMETRY_CACHE` | (unset) | Directory for lock-free per-leaf geometry-hash cache (`leaves/*.ghash`) |
| `NAVI_MWM_GEOMETRY_CACHE_ENABLE` | `1` when cache path set | Read geometry-hash cache during elevation/wetland bake |
| `NAVI_MWM_GEOMETRY_CACHE_WRITE` | `1` when cache path set | Write/update per-leaf `.ghash` after bake |
| `NAVI_MWM_CACHE_IDENTITY_GATE` | `1` | Before a full comaps-release bake, cold-vs-cache-hit identity on ~20 leaves (data sections). `0`/`false`/`off` skips |
| `NAVI_MWM_CACHE_IDENTITY_LEAVES` | (built-in ~20) | Optional leaf list file for the identity gate |
| `NAVI_MWM_ELEV_POLICY_VERSION` | `1` | `1` = uniform 2 m; `2` = Copernicus-only &lt;60°N uses 5 m (next release default) |
| `NAVI_MWM_REGION_MAP` | `config/mwm/catalog/region_leaf_mapping.json` | Region→leaf (migration) |
| `NAVI_MWM_DISPLAY_OVERRIDES` | `config/mwm/catalog/display_name_overrides.json` | Leaf display-name overrides |
| `NAVI_MWM_BORDERS_DIR` | empty | CoMaps `data/borders` (read-only; enables mapping step) |
| `NAVI_MWM_EXTRACTS_DIR` | `$NAVI_EXTRACTS_DIR` | Geofabrik `.poly` extracts (read-only) |
| `NAVI_MWM_REGIONS_JSON` | empty | Published `.rkyv` `current.json` (region list) |

Auto concurrency:

```
max(1, floor( (MemAvailable_GiB − RAM_HEADROOM_GIB) / PER_LEAF_GIB ))
```

On media (~41 GiB MemAvailable idle): **3**. Explicit
`NAVI_MWM_PROCESS_CONCURRENCY=N` wins.

### Disk: `.rkyv` (old) vs MWM sidecars (new)

Measured on the live media host (`/media/navi/navi-server`, 2026-11-15).
CoMaps leaf `.mwm` files are **not** published by Navi (clients fetch those
from the CoMaps CDN); Navi only publishes NVSC sidecars + catalog.

| Tree | What | Logical size (`du --apparent-size` / file sum) | On disk (`du`, ZFS compressed) | Files / regions |
|------|------|-----------------------------------------------|--------------------------------|-----------------|
| **`.rkyv` packs** | `data/published/packs/` + root `current.json` | **~1.65–1.7 TiB** | **~656 GiB** | ~100k files / 540 regions |
| **MWM publish** | `data/published/mwm/` (release `261105`) | **~2.06 GiB** | **~2.1 GiB** | ~1168 files / world leaf set |
| Ratio | published Navi payload | MWM ≈ **1/800** of `.rkyv` logical | MWM ≈ **1/300** of `.rkyv` on disk | — |

**Planning a new host**

| Path | Need roughly |
|------|----------------|
| Serve `.rkyv` + MWM (current freeze) | ≥ **~700 GiB** free for published trees alone (packs compressed + mwm); prefer **≥ 2 TiB** pool if storing uncompressed logical / extra gens |
| MWM-only after client cutover | **~3–5 GiB** for publish + headroom (one release ≈ 2.1 GiB; keep 2 releases + grace) |
| Optional geometry-hash cache | up to **~13 GiB** (scratch world measured) |
| Mapterhorn tile cache | cap **54.72 GiB** (`NAVI_MWM_MAPTERHORN_CACHE_MAX_BYTES`) |
| migrate-mwm (no caches) | transfer **~2.06 GiB** (1180 files including `config/mwm`) |
| migrate-server packs component | transfer **~1.7 TiB** logical `.rkyv` (exclude `mwm/`) |

### Disk budget (bake host)

| Item | Budget / note |
|------|----------------|
| Pool | Size for chosen path above; leave headroom for scratch / downloads |
| Mapterhorn cache default | **54.72 GiB** (`58755104232` B) — Nordic end ~8.5 GiB; 261105 world end 45.60 GiB (post-LRU) + 20% |
| World work tree | CoMaps MWMs deleted after each leaf; sidecars + stats remain |
| Publish | Sidecars only (~2.1 GiB world release `261105`; Nordic much smaller) |

---

## Timers (systemd/)

| Unit | Role |
|------|------|
| `navi-mwm-check.timer` | Daily 04:15 — discover new CoMaps release; bake if enabled |
| `navi-mwm-check.service` | Check + conditional bake |
| `navi-mwm-bake.timer` | Legacy biweekly — **generate test-only** |
| `navi-mwm-bake.service` | `NAVI_MWM_ENABLED=0` in unit; `EnvironmentFile=-/etc/navi/mwm.env` |

Install only after apply review. Check timer may run with bake still gated by
`ENABLED=0`.

---

## Cache

- Location: `NAVI_MWM_MAPTERHORN_CACHE` (persistent across CoMaps releases).
- Eviction: mtime **LRU** when over `NAVI_MWM_MAPTERHORN_CACHE_MAX_BYTES`.
- Each leaf elevation stats JSON includes `tile_cache_hits`, `tile_cache_misses`,
  `tile_cache_hit_rate`. Run summary aggregates hit rate.

---

## Lock and retention

- `mwm_with_lock` / flock on `NAVI_MWM_LOCK` for the whole bake.
- `retain-mwm.sh` after publish: keep current + grace + `KEEP_RELEASES`.
- Never delete the version named by `current.json`.

---

## Install (new host)

```bash
# Non-root: dirs, venv, cargo build (convert + navi-mwm-sidecar)
./scripts/setup-server.sh --dry-run    # preview
./scripts/setup-server.sh

# Root: Apache DocumentRoot = data/published (serves /mwm/ + /packs/ if present)
sudo ./scripts/setup-server.sh --apply-apache
# Root: service user, MWM check timer (ENABLED=0), .rkyv bake/scrub installed but disabled
sudo ./scripts/setup-server.sh --apply-service
# Opt-in weekly .rkyv bake (not default):
# sudo ./scripts/setup-server.sh --apply-service --enable-rkyv-bake

./scripts/setup-server.sh --check
```

`routes_builder_tool` is optional (route validation only); build with
`scripts/mwm/build-generator.sh` when needed.

## Migrate MWM data only (`scripts/migrate-mwm.sh`)

Copies **only** allow-listed MWM paths. Dry-run by default.

| Copied | Not copied |
|--------|------------|
| `data/published/mwm/**` (sidecars, checksums, catalog, attribution, provenance, `current.json`, `.navi-mwm-owned`) — **~2.1 GiB** world release | `.rkyv` `packs/` (**~1.7 TiB** logical) , generations, root `published/current.json` |
| `config/mwm/**` (pipeline config) | scratch, staging, logs, locks, CoMaps leaf `.mwm` downloads |
| Optional: `--with-mapterhorn-cache`, `--with-geometry-cache`, `--with-secrets` | old elevation datasets (Copernicus/SRTM/…) |

```bash
# Preview exact file list + size (holds pack-pipeline.lock on source)
./scripts/migrate-mwm.sh --source /media/navi/navi-server --dest /path/to/new-root

# Apply + SHA-256 verify (current.json copied last)
./scripts/migrate-mwm.sh --source /media/navi/navi-server --dest /path/to/new-root --apply

# Optional caches (can be large — dry-run first)
./scripts/migrate-mwm.sh --source … --dest … --with-mapterhorn-cache --dry-run
```

Full-server migrate (packs/datex/secrets, **excludes** `mwm/`): `scripts/migrate-server.sh`.

### New host bring-up

1. Install OS deps + clone repo → `setup-server.sh` → `--apply-apache` → `--apply-service`
2. `migrate-mwm.sh --apply` from the live source (or rsync over ssh)
3. `setup-server.sh --check` + curl `/mwm/current.json`
4. Switch DNS / DuckDNS to the new host

---

## Enable on live (after review)

1. Review and run `/tmp/navi-mwm-apply.sh` (or operator equivalent): paths,
   timers, `mwm.env`.
2. Keep `NAVI_MWM_ENABLED=0` until a scratch world/Nordic publish looks good.
3. Set `NAVI_MWM_ENABLED=1` in `/etc/navi/mwm.env`.
4. Enable `navi-mwm-check.timer` only.
5. Confirm `published/mwm/current.json` updates; **do not** change `.rkyv`
   `published/current.json`.

### Rollback

1. Set `NAVI_MWM_ENABLED=0`.
2. Stop/disable MWM timers.
3. Optionally remove or ignore `published/mwm/current.json` (clients treat
   missing catalog as “MWM not offered”).
4. `.rkyv` path unchanged.

---

## Scratch commands

```bash
export NAVI_MWM_ENABLED=0
export NAVI_MWM_MODE=comaps-release
export NAVI_MWM_PUBLISHED_ROOT=/path/to/scratch-published
export NAVI_MWM_DOWNLOAD_DIR=/path/to/scratch-download
export NAVI_MWM_MAPTERHORN_CACHE=/path/to/mapterhorn-cache
export NAVI_MWM_PROCESS_CONCURRENCY=3   # or 0 for auto
# Nordic leafset (default):
./scripts/mwm/run-mwm-bake.sh --force --publish-root "$NAVI_MWM_PUBLISHED_ROOT"
# World:
NAVI_MWM_ALL_LEAVES=1 \
  ./scripts/mwm/run-comaps-release-bake.sh --force --publish-root "$NAVI_MWM_PUBLISHED_ROOT"
```

Summarize a world work dir:

```bash
python3 scripts/mwm/summarize-world-run.py --work "$NAVI_MWM_DOWNLOAD_DIR/<ver>"
```

---

## Client docs index

| Doc | Audience |
|-----|----------|
| [client-fetch-mwm.md](client-fetch-mwm.md) | Download + catalog contract |
| [sidecar-format.md](sidecar-format.md) | NVSC bytes |
| [client-routing-hooks.md](client-routing-hooks.md) | Router behaviour |
| [client-migration-from-rkyv.md](client-migration-from-rkyv.md) | Migration plan |
| [mwm-planet.md](mwm-planet.md) | generate-mode planet notes (test-only) |
