# Geofabrik `.osc.gz` incremental extract updates

**Status (2026-09-10):** Wired into **`run-weekly.sh` for all regions** in
`NAVI_REGIONS_CONF` via `fetch-extracts.sh --prefer-incremental`. Exit **10**
still falls through to the existing full-fetch path unchanged.
`--force-fetch` on the weekly job skips incremental (full fetch only).

**Not** wired into planet-leaves / batched full-planet bakes (those stay on
the larger box). Target host for the Monday-midnight weekly job: **netcup VPS
2000 G12** (8 vCore / 16 GiB DDR5 / 512 GB NVMe). Convert concurrency on that
path is solely `WorkerPoolPlan` inside `navi-indexed-convert` (cores +
`MemAvailable`); weekly does not set `NAVI_TILE_BUILD_CONCURRENCY`.

See also [`docs/vps-prelaunch-checklist.md`](vps-prelaunch-checklist.md).

## Why

Routine refreshes should apply Geofabrik’s regional change files to a
**held** `.osm.pbf` instead of re-downloading the full extract every time.
A full fetch remains required for first-ever bakes and when the held extract
is older than Geofabrik’s diff retention window (~100 days).

## Geofabrik replication (inspected)

For `north-america/us/west-virginia`:

| Item | Value |
|------|--------|
| Updates URL | `https://download.geofabrik.de/north-america/us/west-virginia-updates/` |
| Layout | Osmosis-style `AAA/BBB/CCC.osc.gz` + matching `.state.txt` |
| Root `state.txt` | `sequenceNumber` + `timestamp=` (colons often escaped) |
| PBF headers | `osmosis_replication_base_url`, `_sequence_number`, `_timestamp` (readable via `osmium fileinfo -j`) |
| Retention (measured) | Oldest available WV diff ≈ seq **4770** while tip ≈ **4902** (~**132** daily sequences); docs say ~100 days / ~3 months |

`state.txt` at the updates root can lag the newest numbered `.state.txt` by
one sequence; the updater probes forward from the root hint.

**Hedmark** in `regions.conf` uses OSM.fr (`url:…`), not Geofabrik — the
incremental path does **not** apply (full fetch only). Use a Geofabrik
region (e.g. West Virginia) for this mechanism.

## Tooling / dependencies

| Tool | Role | New package? |
|------|------|----------------|
| **`osmium`** (`osmium-tool`) | Read headers (`fileinfo -j`), `apply-changes`, rewrite headers (`cat -f pbf`) | **No** — already on this host / used by the project |
| **Python 3 stdlib** + `urllib` | Fetch `state.txt` / `.osc.gz`, decision helpers | **No** |
| **`curl`** | Existing full-fetch path unchanged | **No** |
| `pyosmium` / `pyosmium-up-to-date` | Geofabrik-documented alternative | **Not required**; optional `apt install pyosmium` if you prefer that CLI later |

Flag: we deliberately **did not** add a pyosmium pip/apt dependency. The
implementation is `scripts/lib/geofabrik_replication.py` +
`scripts/fetch-incremental.sh`.

## Decision logic

Centralized in `scripts/fetch-incremental.sh` (enabled for every weekly region
by `run-weekly.sh` → `fetch-extracts.sh --prefer-incremental`):

1. **Source is not `geofabrik:*`** → exit 10 → full fetch.
2. **No prior published pack** for the region → exit 10 → full fetch
   (first-ever bake).
3. **No held PBF** at `region_pbf_path` (or `--held-pbf`) with replication
   headers → exit 10 → full fetch (published pack alone is not enough).
4. **Held state outside retention** (age &gt; `NAVI_GEOFABRIK_DIFF_RETENTION_DAYS`
   default **100**, or next `.osc.gz` missing) → log
   `incremental unavailable, falling back to full` → exit 10.
5. Else apply diffs (or no-op if already at tip) → exit 0.

`fetch-extracts.sh --prefer-incremental` tries (5) then falls back to the
existing full download on exit 10. **`--force` / weekly `--force-fetch` skips
incremental** and always full-fetches.

### Held-PBF retention (size policy)

`cleanup.sh` **does not** age-delete extracts by default. Both
`run-weekly.sh` and `systemd/navi-pack-scrub.service` pass `--no-extracts`.
Opt in to age-delete with `--prune-extracts` or `NAVI_SCRUB_PRUNE_EXTRACTS=1`.

Independent of age-delete, cleanup enforces a **disk budget** sized for the
minimum weekly host (8 vCore / 16 GiB / **512 GB** NVMe):

| Knob | Default | Effect |
|------|---------|--------|
| `NAVI_HELD_PBF_MAX_MIB` | **512** | Drop any held `*-latest.osm.pbf` larger than this (0 = off) |
| `NAVI_HELD_PBF_BUDGET_GIB` | **40** | Cap total held PBF bytes; when over, drop **largest first** so more small/medium regions stay on incremental (0 = unlimited) |

In-flight temps (`*partial*`) are never deleted by this policy. Dropped
regions fall through to full Geofabrik fetch on the next weekly (exit 10) —
that is intentional, not a failure.

**Why this shape (measured against `regions.planet.conf`, 522 Geofabrik
extracts, 2026-09-10 HEAD sizes):**

- All 522 held = **114.9 GiB**; top 10 alone = **38.1 GiB (33%)**. Holding
  everything with 331 GiB of published packs left only ~10% free on a 512 GB
  root — too tight for staging/scratch.
- Max 512 MiB drops 38 monsters (~57 GiB). Budget 40 GiB then keeps ~435 of
  the remaining (preferring smaller) ≈ **39.8 GiB** held.
- Envelope: packs 331 + held ≈40 + scratch ≈25 ≈ **396 GiB** on ~495 GiB
  usable → **~99 GiB free (~20%)**. Comfortable vs the prior ~6–10%.

Large bake hosts may set both knobs to `0` to retain everything.
### Post-diff sequence tip check

Before the atomic replace of a held PBF, both
`geofabrik_replication.py` and `fetch-incremental.sh` re-read
`osmosis_replication_sequence_number` and require it to equal the expected tip
(`SEQUENCE_TIP_VERIFY=PASS|FAIL`). Mismatch aborts without replacing the held
file.

### Checksum re-verify (publish)

`publish-packs.sh` verifies each region’s currently-live published
`checksums.sha256` **before** flipping the internal `live` symlink and before
pruning prior HTTP gens (`stage=pre-swap-live`), then verifies the newly
written gen (`stage=post-write-new`). Signals:
`CHECKSUM_REVERIFY=PASS|FAIL`. On FAIL: no symlink swap (pre-swap), no prune,
nothing deleted. Helper: `scripts/verify-published-checksums.sh`.

## Isolated West Virginia test (not published)

Work dir: `data/scratch/incremental-test/` (does not touch planet-leaves
scratch/session). Historical smoke from 2026-09-06:

| Step | Result |
|------|--------|
| Base | Geofabrik dated `west-virginia-260830.osm.pbf` (seq **4897**, 2026-08-30) |
| Tip | `west-virginia-latest.osm.pbf` (seq **4902**) |
| Diffs applied | seq **4898..4902** (5 files), **535 985** bytes downloaded |
| Wall time apply | ~**11 s** |
| Full latest PBF download | **98 655 263** bytes (~**94 MiB**); ~1.3 s on a warm path / ~149 s for the dated file earlier |
| Transfer ratio | Diff path ≈ **0.54%** of a full PBF download for this 5-day window |
| Object counts | Base→updated: nodes +13883, ways +1695, relations +51; **updated ≡ tip** counts |
| Zero-diff | Re-run on updated file → `already_current`, 0 bytes |
| Convert + validate | Existing `convert-region.sh` + `validate-packs.sh --from-convert` on isolated extracts/convert dirs → **PASS** (failures=0). Output **not** published over production packs. |

## Rollout

Done in-tree for the weekly path:

1. Held-PBF retention on weekly cleanup (`--no-extracts`).
2. `run-weekly.sh` → `--prefer-incremental` for all conf regions (exit 10 → full).
3. Sequence-tip verify before held-PBF atomic replace.
4. Pre-swap + post-write checksum re-verify in `publish-packs.sh`.
5. Small-VPS resource profile: sequential regions + `WorkerPoolPlan` only.

Still operator / cutover work (not automated here):

1. Point VPS `NAVI_REGIONS_CONF` at the published set (e.g. `regions.planet.conf`).
2. Refresh scrub unit if an older install lacks `--no-extracts` (shipped unit
   already keeps extracts; do not set `NAVI_SCRUB_PRUNE_EXTRACTS=1` on VPS).
3. Confirm VPS `config.env` does not pin `NAVI_TILE_BUILD_CONCURRENCY`.
4. Netcup snapshot / rollback orchestration — **not** wired; verify-gate log
   lines are the fail signal until that exists.

Regression scripts (isolated `mktemp` roots; no live published mutation):

- `scripts/test-geofabrik-replication-unit.sh`
- `scripts/test-verify-published-checksums.sh`
- `scripts/test-publish-preprune-checksum.sh`
- `scripts/test-weekly-held-pbf-retention.sh`
- `scripts/test-scrub-held-pbf-default.sh`
- `scripts/test-held-pbf-budget.sh`
- `scripts/test-weekly-incremental-wiring.sh`
- `scripts/test-weekly-workerpool-profile.sh`
