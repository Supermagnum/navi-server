# Client fetch contract — CoMaps MWMs + Navi sidecars

This document is the **client-facing contract**. A coding assistant working in
the Navi client repo should be able to implement download, verify, switch, and
offline attribution from this file alone (plus
[sidecar-format.md](sidecar-format.md) and
[client-routing-hooks.md](client-routing-hooks.md)).

Server-side bake stays off by default (`NAVI_MWM_ENABLED=0`). Missing
`/mwm/current.json` on the public host means “MWM sidecars not offered yet”.

The existing `.rkyv` catalog (`/current.json`, filesystem
`published/current.json`) is **unchanged**.

---

## 1. Overview

| Asset | Source | Notes |
|-------|--------|-------|
| Base map `{Leaf}.mwm` | CoMaps mirrors | Discovered via metaserver; never mixed across releases |
| Sidecar `{Leaf}.navi-sidecar.v1.zst` | navi-server | Wetland Soft/Hard, boardwalk, elevation, optional `vehicle_limits` (VL01) + `named_routes` (NR01) |
| Catalog + attribution | navi-server | `/mwm/current.json`, `/mwm/{version}/attribution.json`, `ATTRIBUTION.md` |

**Never mix releases across leaves.** Every leaf in a Navi region must come from
the same CoMaps `release_version` (catalog `version`, e.g. `261105`). Mixing
leaves from two releases breaks routing topology and sidecar fingerprints.

---

## 2. Published layout and URLs

### Public HTTP (DocumentRoot)

Apache `DocumentRoot` is the server’s `data/published/` directory. Clients must
use **DocumentRoot-relative** paths under **`/mwm/`**, never `/published/mwm/`.

**Public base URL (live):** `https://navigate-me.duckdns.org/mwm/`

```
https://navigate-me.duckdns.org/mwm/
  current.json                 # atomic pointer (written last)
  {version}/                   # e.g. 261105
    catalog.json
    attribution.json
    ATTRIBUTION.md
    checksums.sha256
    discovery.json             # optional; mirrors server discovery metadata
    country_groups.json
    region-migration.json
    {Leaf}.navi-sidecar.v1.zst
```

| Resource | Example |
|----------|---------|
| Current catalog | `GET https://navigate-me.duckdns.org/mwm/current.json` |
| Versioned catalog | `GET https://navigate-me.duckdns.org/mwm/261105/catalog.json` |
| Sidecar | `GET https://navigate-me.duckdns.org/mwm/261105/Norway_Hedmark.navi-sidecar.v1.zst` |
| Attribution JSON | `GET https://navigate-me.duckdns.org/mwm/261105/attribution.json` |
| Human attribution | `GET https://navigate-me.duckdns.org/mwm/261105/ATTRIBUTION.md` |

Relative `sidecar_url` in the catalog is under the version directory
(e.g. `261105/Norway_Hedmark.navi-sidecar.v1.zst`). Resolve against the public
base `https://navigate-me.duckdns.org/mwm/` (or `{navi-origin}/mwm/` on another
host with the same DocumentRoot layout).

`GET …/published/mwm/…` returns **404** on the live host — that prefix is the
on-disk tree name under DocumentRoot, not part of the URL.

### On-disk tree (operators)

Filesystem publish root remains `data/published/mwm/` (`NAVI_MWM_PUBLISHED_ROOT`).
That is what Apache serves as `/mwm/`.

---

## 3. Catalog schema `navi-mwm-catalog/v2`

Normative example (subset): [examples/catalog-v2.example.json](examples/catalog-v2.example.json).
CI validates that example against the field rules below. Hedmark sidecar
bytes/SHA in the example are pinned to a checked-in fixture (see
[sidecar-format.md](sidecar-format.md) §10).

### Top level

| Field | Type | Meaning |
|-------|------|---------|
| `schema` | string | Must be `navi-mwm-catalog/v2` |
| `mode` | string | `comaps-release` (production path) |
| `version` | string | CoMaps data-version directory name (`"261105"`) |
| `comaps` | object | CoMaps discovery metadata (see below) |
| `generator` | object | Navi sidecar tool pin: `tag`, `sha`, `role` |
| `limitation` | string | Human note about shared leaves / coverage |
| `leaves` | array | One entry per published leaf (see below) |
| `region_to_leaves` | object | Navi `.rkyv` bake_id → list of CoMaps leaf ids (migration only) |
| `shared_leaves` | object | Leaf id → list of Navi bake_ids that share it |
| `country_groups` | array | Country-grouped leaf list for the **region UI** (see §5) |
| `country_groups_url` | string\|null | `{version}/country_groups.json` (same payload; optional fetch) |
| `display_name_overrides` | object | Leaf id → current display name / notes (Norway seeded) |
| `region_migration_url` | string\|null | `{version}/region-migration.json` (overlap detail for migrators) |
| `world_leaf_count` | number | Count of leaves in the CoMaps `countries.txt` for this release |

### `comaps` object

| Field | Type | Meaning |
|-------|------|---------|
| `map_series` | string | e.g. `2026.10.27` |
| `release_version` | number | Same as `version` as integer (`261105`) |
| `countries_txt_sha256` | string | SHA-256 of verified `countries.txt` |
| `countries_txt_bytes` | number | Byte length |
| `countries_txt_sig_verified` | bool | Server verified Ed25519 before bake |
| `countries_txt_sig_sha256` | string | SHA-256 of the 64-byte signature |
| `countries_txt_pubkey_hex` | string | CoMaps app pubkey (see §4) |
| `metaserver_url` | string | e.g. `https://cdn-us-1.comaps.app` |
| `maps_json_url` | string | `{metaserver}/meta/maps.json` |
| `note` | string | Reminder: discover mirrors; do not hard-code |

### Each `leaves[]` entry

| Field | Type | Meaning |
|-------|------|---------|
| `id` | string | CoMaps leaf id (`Norway_Hedmark`) |
| `sidecar_url` | string | Relative URL under `/mwm/` (e.g. `261105/Norway_Hedmark.navi-sidecar.v1.zst`) |
| `sidecar_bytes` | number | Sidecar size |
| `sidecar_sha256` | string | SHA-256 of sidecar bytes |
| `comaps_file` | string | `{id}.mwm` |
| `comaps_bytes` | number | Expected MWM size from `countries.txt` (`s`) |
| `comaps_sha1_base64` | string | CoMaps SHA-1 (base64) for the MWM (`sha1_base64`) |
| `mwm_bytes` | number | Fingerprint size of the MWM used at bake |
| `mwm_sha256` | string | Fingerprint SHA-256 of that MWM |
| `mwm_fingerprint` | object | `{size, sha256}` — same values; prefer for checks |
| `shared` | bool | True if the leaf appears in multiple Navi regions |
| `shared_with_regions` | string[] | Present when `shared` |
| `no_sidecar` | bool | Optional. `true` for non-routable CoMaps overlays (`World`, `WorldCoasts`) — clients must not expect a Navi sidecar |
| `sidecar` / `sidecar_url` | null | Set when `no_sidecar` is true |
| `elevation_policy_version` | number\|null | Sidecar elevation policy (`1` = uniform 2 m; `2` = Copernicus-only &lt;60°N uses 5 m). `null` when `no_sidecar` |
| `vehicle_limits_coverage` | object\|omit | Per-leaf match coverage (optional; required before live world publish). See below |
| `named_routes_coverage` | object\|omit | Per-leaf named-route stub counts when NR was annotated. See below |

`vehicle_limits_coverage` (when present):

| Field | Type | Meaning |
|-------|------|---------|
| `tagged_ways` | number | OSM ways with a vehicle-limit tag in the leaf |
| `matched_ways` | number | Ways that produced at least one sidecar record |
| `match_rate` | number | `matched_ways / numeric_ways` (specials excluded) |
| `non_numeric_no_limit` | number | `none` / `default` / `unsigned` / `no-limit` |
| `unknown_value` | number | `below_default` (report only; not in records) |
| `osm_data_date` | string | OSM extract date (`YYYY-MM-DD`) or empty |
| `map_release` | string | CoMaps release id (e.g. `261105`) |

The same fields are embedded in the sidecar `vehicle_limits` VL01 header
([sidecar-format.md](sidecar-format.md) §5). Clients treat restrictions as
**fully applied** for a leaf when `match_rate ≥` a configurable threshold
(default **0.90**); below that (or when coverage is absent) they still apply
matched records but warn that restrictions are only partly available.

`named_routes_coverage` (when present):

| Field | Type | Meaning |
|-------|------|---------|
| `routes` | number | Stub count written for the leaf |
| `by_kind` | object | Map of kind string → count (e.g. `hiking`, `pilgrimage`, `ski`) |

Absent when `NAVI_MWM_NAMED_ROUTES_PBF` was unset for the bake (section omitted).
Wire layout NR01: [sidecar-format.md](sidecar-format.md) §6. Clients use stubs
for search / browse only — not for edge costs.

**Same-release sidecar refresh:** when the catalog version matches the active
release but a leaf’s `sidecar_sha256` / `sidecar_bytes` changed, re-download
only that sidecar — do not re-fetch the MWM.

Live publish gate: coverage recorded for every routable leaf (not “every
country ≥ 0.90”).

Top-level catalog also carries `elevation_policy_version` (one policy per release) and
`non_routable_leaves` (`["World","WorldCoasts"]`).

### Non-routable leaves (no sidecar)

`World.mwm` and `WorldCoasts.mwm` are CoMaps overlay / coastline datasets without a
per-country road network for Navi wetland or elevation routing. Navi **does not**
bake or publish sidecars for them (they previously drove multi-GiB elevation RSS).
Clients still download those MWMs from CoMaps when the app needs them; catalog
entries are present with `no_sidecar: true` so fetchers can skip sidecar URLs.

### Version / compat

- Clients that understand `navi-mwm-catalog/v2` may ignore unknown **optional**
  top-level keys.
- Unknown `schema` values → refuse to consume (show “update app”).
- Sidecar format version is independent (`NVSC` v1); see sidecar-format.md.

---

## 4. CoMaps discovery (client must do this)

Mirror the CoMaps app. Do **not** hard-code a mirror list.

1. `GET {metaserver}/meta/maps.json` → read `map-series[{series}].latest` = `V`.
2. `GET {metaserver}/servers` with header `X-OM-DataVersion: V` (and a normal
   User-Agent) → **JSON array of mirror base URLs**, ordered **best-first**
   (see §4.1).
3. `GET {mirror}/maps/{series}/{V}/countries.txt` and
   `GET …/countries.txt.sig` (exactly **64** bytes).
4. Verify Ed25519:
   - Public key (hex): `91c0a9f6aa182371f047e256ab46489211acc2b51b13197fbe8c94eaa9749c7b`
     (CoMaps `COUNTRIES_TXT_SIGNATURE_HEX` / `private.h`; also in catalog
     `comaps.countries_txt_pubkey_hex`).
   - Message = raw `countries.txt` bytes; signature = raw `.sig` bytes.
5. Parse leaf `id`, size (`s`), and `sha1_base64` from `countries.txt` (JSON —
   see §4.2).
6. Download `GET {mirror}/maps/{series}/{V}/{UrlEncode(id)}.mwm`.
   - URL-encode like CoMaps (`quote` with safe `-_.`); spaces become `%20`.
7. Verify **size** and **SHA-1 (base64)** against `countries.txt` / catalog.

### 4.1 `/servers` response

Live metaserver returns a **JSON array of strings** (mirror base URLs), for
example:

```json
[
  "https://cdn-fi-1.comaps.app/",
  "https://cdn-us-2.comaps.tech/",
  "https://comaps.firewall-gateway.de/",
  "https://comaps-it1.unfoxo.it/",
  "https://comaps.openstreetmap.fr/"
]
```

**Order matters:** index `0` is the preferred mirror; try mirrors in array
order. On hard failure (DNS/connect/HTTP 5xx after retries), fail over to the
next URL. Do not shuffle or hard-code a different preference unless product
policy says otherwise. Soft failures (429) should backoff on the current mirror
before skipping.

### 4.2 `countries.txt` format (JSON)

Despite the historic `.txt` name, the CoMaps body is **JSON** (UTF-8). Release
`261105` is about **315504** bytes; SHA-256 must match
`comaps.countries_txt_sha256` in the catalog.

Top-level object:

| Field | Type | Meaning |
|-------|------|---------|
| `id` | string | Root id (`"Countries"`) |
| `v` | number | Release version (`261105`) |
| `map_series` | string | Series string (e.g. `2026.10.27`) |
| `g` | array | Hierarchy of groups and leaves |

Each node in `g` (and nested `g`) may include:

| Field | Type | Meaning |
|-------|------|---------|
| `id` | string | Group or leaf id (`Norway_Hedmark`) |
| `s` | number | MWM byte size (leaves) |
| `sha1_base64` | string | MWM SHA-1, base64 (leaves) |
| `affiliations` | string[] | Optional display / search strings |
| `g` | array | Optional nested children |

Clients need leaf `id` + `s` + `sha1_base64` for download verify. Abbreviated
real excerpt: [examples/countries-txt.excerpt.json](examples/countries-txt.excerpt.json).

### Polite-client rules

| Rule | Guidance |
|------|----------|
| User-Agent | Identify the app + contact; e.g. `Navi/… (+https://…; contact=…)` |
| Concurrency | ≤ 2 MWM downloads at once (navi-server uses 2) |
| Resume | HTTP Range / `.part` files; retry on transient errors |
| Backoff | Exponential backoff with jitter on 429/5xx |
| Failover | Try next mirror from `/servers` (array order) on hard failure |

---

## 5. Region UI (`country_groups`) vs `.rkyv` migration (`region_to_leaves`)

Two related structures — **do not mix their roles**.

### 5a. Country-grouped leaf list (primary region UI)

**Use `country_groups` (or `country_groups_url`) for the download UI.**

Built from CoMaps `countries.txt` hierarchy each release:

| Field | Meaning |
|-------|---------|
| `id` / `display_name` | CoMaps country or subgroup name |
| `parent_groups` | Parent ids as CoMaps defines them (e.g. state under USA) |
| `leaves[]` | Routable leaves: `id`, `display_name`, `mwm_bytes`, `sidecar_bytes`, `total_bytes`, … |
| `subgroups[]` | Nested groups (US states, etc.) with the same shape |
| `leaf_count` / `total_*_bytes` | Rollups |

`display_name_overrides` (catalog top-level, and in `country_groups.json`) maps
CoMaps leaf ids to **current** names where CoMaps still ships outdated labels.
Norway is seeded (Hedmark/Oppland → Innlandet, Hordaland → Vestland, Nord-/Sør-Trøndelag → Trøndelag). Other countries with likely outdated names are listed under `likely_outdated_elsewhere` **without** guessed overrides — review before adding.

A few disputed territories appear under more than one parent in `countries.txt`
(Crimea, Abkhazia, …). Treat the **unique** leaf id as one download; the
hierarchy may show the leaf in multiple groups for navigation.

### 5b. `.rkyv` → leaves migration table

**Use only when moving users who already downloaded a Navi `.rkyv` pack.**

| Artifact | Role |
|----------|------|
| `region_to_leaves[bake_id]` | Compact leaf id list (also in catalog) |
| `shared_leaves[leaf]` | bake_ids that share that leaf |
| `region_migration_url` → `region-migration.json` | Full overlap detail |

`region-migration.json` schema `navi-mwm-region-migration/v1` (per region / bake_id):

| Field | Meaning |
|-------|---------|
| `region_id` | Geofabrik-style path (`europe/norway/ostlandet`) |
| `display_name` | Human label |
| `union_coverage_pct` / `uncovered_pct` | Land overlap vs gap (ocean/fjord often explains gaps) |
| `comaps_leaves[]` | `leaf`, `coverage_of_region_pct`, `overspill_pct`, `include_reason`, `shared_with_navi_regions` |
| `shared_leaves` / `review` | Cross-region shares; gaps & “too many leaves” for operators |

**Include threshold (default 0.5% of region area):** below that, numeric border
noise dominates. CoMaps borders are land-only, so stable land overlaps ≥ 0.05%
are also kept (`micro_land_overlap`) as a proxy for border roads/places. Leaves
with ≥50% of their area outside the Navi region are kept only when they still
cover ≥25% of that region — otherwise neighbouring-country coastal noise
(Geofabrik sea/fjord ∩ foreign land borders) pollutes `region_to_leaves`
(e.g. DK/SE under Østlandet). Thresholds are configurable on
`build-region-mapping.py`.

Server source files: `config/mwm/catalog/region_leaf_mapping.json`,
`display_name_overrides.json`; regenerated every CoMaps release by the MWM
pipeline (with a diff against the previous `region-migration.json`).

Until MWM is the sole client path, `.rkyv` packs stay served from
`/current.json` (filesystem `published/current.json`). See
[client-migration-from-rkyv.md](client-migration-from-rkyv.md).

---

## 6. Release switching

1. Read new `/mwm/current.json` (or poll `comaps.release_version`).
2. Resolve the region’s leaf list for the **new** version.
3. Download **all** new `.mwm` files (CoMaps) + matching sidecars (navi-server)
   into a staging directory.
4. Verify sizes, SHA-1 (MWM), SHA-256 (sidecar), and **fingerprints** (§7).
5. Atomically switch the app’s active release pointer to the staging set.
6. Keep the previous release on disk for the server grace window
   (`NAVI_MWM_GRACE_DAYS`, default **7** days after supersede). A release
   still named by `current.json` is never deleted on the server.

### Mid-download failure

- Leave the previous release active.
- Retry failed leaves; do not partially activate a mixed set.
- If the new release is abandoned, delete the incomplete staging tree.

---

## 7. Fingerprint rule (mandatory)

After downloading a sidecar, decompress enough of the NVSC header to read
`mwm_size` + `mwm_sha256` (see sidecar-format.md), **or** trust
`leaves[].mwm_fingerprint` only after verifying the sidecar SHA-256 matches
the catalog.

**Never apply** a sidecar unless:

```
local_mwm.stat().st_size == fingerprint.size
AND sha256(local_mwm) == fingerprint.sha256
```

On mismatch: discard/ignore that sidecar for the leaf; route **without** Navi
wetland/elevation layers for that leaf; surface a clear warning
(“Navi layers unavailable — map build mismatch”).

---

## 8. Offline attribution

Ship and display offline (settings / about / first map load as product requires):

| Credit | Source |
|--------|--------|
| OpenStreetMap | `attribution.json` / `ATTRIBUTION.md` |
| CoMaps + mirrors | same |
| Mapterhorn | same (elevation) |
| Navi | same |

Do not strip credits from the attribution section of the sidecar.

---

## 9. Errors and fallbacks

| Failure | Client behaviour |
|---------|------------------|
| navi-server unreachable | Keep last good release; no sidecar update |
| CoMaps mirror unreachable | Fail over mirrors; then keep last good MWMs |
| `countries.txt` signature fail | **Abort** that release; do not download MWMs |
| Missing sidecar for a leaf | Use MWM alone; warn that Navi layers are off |
| Fingerprint mismatch | Same as missing sidecar for that leaf |
| Catalog schema unknown | Prompt app update; keep previous format |

---

## 10. Fetch order

### New install / region UI

1. `GET https://navigate-me.duckdns.org/mwm/current.json` (or `{origin}/mwm/current.json`)
2. Present `country_groups` (apply `display_name_overrides`)
3. User picks leaves (or a whole country/subgroup)
4. Discover CoMaps mirrors; download each `{leaf}.mwm`; verify size + SHA-1
5. Download each sidecar; verify SHA-256
6. Enforce fingerprint (§7)
7. Apply wetland / boardwalk / elevation per [client-routing-hooks.md](client-routing-hooks.md)

### Migrating an existing `.rkyv` download

1. `GET …/mwm/current.json`
2. `leaves = region_to_leaves[bake_id]` (optional: read `region_migration_url` for coverage)
3. Same download / verify steps as above
4. Keep the `.rkyv` pack until the user confirms MWM works
