# Operator guide — CoMaps-release MWM sidecar pipeline

Scratch-first. Default **`NAVI_MWM_ENABLED=0`**. Live bake stays off until
`/tmp/navi-mwm-apply.sh` (or equivalent) is reviewed.

Does **not** touch weekly `.rkyv` bake, scrub, or live `published/current.json`.
Shares `pack-pipeline.lock` with the `.rkyv` pipeline — never run MWM bake while
the weekly bake holds the lock. Pause MWM world runs within **6 hours** of
Monday **00:00 UTC** weekly bake.

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
| `NAVI_MWM_MAPTERHORN_CACHE_MAX_BYTES` | `51539607552` (48 GiB) | mtime-LRU eviction cap; raise to measured+20% after first world run |
| `NAVI_MWM_MAPTERHORN_ZOOM` | `12` | Terrarium zoom |
| `NAVI_MWM_ELEV_THRESHOLD_M` | `2` | Override when \|Δ\| exceeds this |
| `NAVI_MWM_PROCESS_CONCURRENCY` | `0` (auto) | `0` → from RAM; override with explicit int |
| `NAVI_MWM_PER_LEAF_GIB` | `8.2` | Measured peak RSS (also `measured-peaks.json`) |
| `NAVI_MWM_RAM_HEADROOM_GIB` | `16` | Reserved when deriving concurrency |
| `NAVI_MWM_DOWNLOAD_CONCURRENCY` | `2` | Parallel MWM HTTP downloads |
| `NAVI_MWM_MEASURED_PEAKS` | `config/mwm/measured-peaks.json` | Peak RSS source |
| `NAVI_MWM_METASERVER` | `https://cdn-us-1.comaps.app` | CoMaps metaserver |
| `NAVI_MWM_MAP_SERIES` | `2026.10.27` | maps.json series key |
| `NAVI_MWM_COMAPS_VERSION` | empty | Pin data-version; empty = latest |
| `NAVI_MWM_GRACE_DAYS` | `7` | Retain superseded releases |
| `NAVI_MWM_KEEP_RELEASES` | `2` | Retain count floor |
| `NAVI_MWM_HTTP_USER_AGENT` | `navi-server-mwm/1.0 (+…)` | Polite client |
| `NAVI_MWM_SKIP_ROUTE_CHECK` | `0` | `1` skips six-route (streaming/low disk) |
| `NAVI_MWM_REGION_MAP` | `config/mwm/catalog/region_leaf_mapping.json` | Region→leaf |

Auto concurrency:

```
max(1, floor( (MemAvailable_GiB − RAM_HEADROOM_GIB) / PER_LEAF_GIB ))
```

On media (~41 GiB MemAvailable idle): **3**. Explicit
`NAVI_MWM_PROCESS_CONCURRENCY=N` wins.

### Disk budget (Mypool/navi)

| Item | Budget / note |
|------|----------------|
| Pool | 1.0 TiB total; leave headroom for `.rkyv` + scratch |
| Mapterhorn cache default | **48 GiB** — fits; Nordic end size ~8.5 GiB; world end size sets measured+20% default |
| World work tree | MWMs deleted after each leaf; sidecars + stats remain |
| Publish | Sidecars only (~tens of MiB Nordic; world scales with leaf count) |

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
NAVI_MWM_ALL_LEAVES=1 NAVI_MWM_SKIP_ROUTE_CHECK=1 \
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
