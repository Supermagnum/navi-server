# Geofabrik `.osc.gz` incremental extract updates

**Status (2026-09-06):** Implemented and tested on **one** region
(`us_west_virginia`) in an isolated scratch tree. **Not** wired into
`run-weekly.sh`, planet-leaves, or any systemd timer. Review results before
production rollout.

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

Centralized in `scripts/fetch-incremental.sh` (called from
`fetch-extracts.sh` only when `--prefer-incremental` is set):

1. **Source is not `geofabrik:*`** → exit 10 → full fetch.
2. **No prior published pack** for the region → exit 10 → full fetch
   (first-ever bake).
3. **No held PBF** at `region_pbf_path` (or `--held-pbf`) with replication
   headers → exit 10 → full fetch (published pack alone is not enough;
   extracts are often cleaned after publish today).
4. **Held state outside retention** (age &gt; `NAVI_GEOFABRIK_DIFF_RETENTION_DAYS`
   default **100**, or next `.osc.gz` missing) → log
   `incremental unavailable, falling back to full` → exit 10.
5. Else apply diffs (or no-op if already at tip) → exit 0.

`fetch-extracts.sh --prefer-incremental` tries (5) then falls back to the
existing full download on exit 10. **`--force` skips incremental** and always
full-fetches.

## Isolated West Virginia test (not published)

Work dir: `data/scratch/incremental-test/` (does not touch planet-leaves
scratch/session).

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

## Rollout (not done yet)

Before enabling on weekly Hedmark/WV schedules:

1. **Retain** a held PBF after successful publish (today cleanup often deletes
   extracts — incremental cannot start from pack files alone).
2. Opt-in: `fetch-extracts.sh --prefer-incremental` from a revised
   `run-weekly.sh` (explicit follow-up).
3. Keep 100-day fallback logging distinct from first-bake full fetches.
