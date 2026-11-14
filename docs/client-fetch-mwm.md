# Client fetch contract — CoMaps MWMs + Navi sidecars

This document is the **client-facing contract**. A coding assistant working in
the Navi client repo should be able to implement download, verify, switch, and
offline attribution from this file alone (plus
[sidecar-format.md](sidecar-format.md) and
[client-routing-hooks.md](client-routing-hooks.md)).

Server-side bake stays off by default (`NAVI_MWM_ENABLED=0`). Missing
`published/mwm/current.json` means “MWM sidecars not offered yet”.

The existing `.rkyv` catalog (`published/current.json`) is **unchanged**.

---

## 1. Overview

| Asset | Source | Notes |
|-------|--------|-------|
| Base map `{Leaf}.mwm` | CoMaps mirrors | Discovered via metaserver; never mixed across releases |
| Sidecar `{Leaf}.navi-sidecar.v1.zst` | navi-server | Wetland Soft/Hard, boardwalk proxy, elevation overrides |
| Catalog + attribution | navi-server | `published/mwm/current.json`, `attribution.json`, `ATTRIBUTION.md` |

**Never mix releases across leaves.** Every leaf in a Navi region must come from
the same CoMaps `release_version` (catalog `version`, e.g. `261105`). Mixing
leaves from two releases breaks routing topology and sidecar fingerprints.

---

## 2. Published layout and URLs

```
{navi-origin}/published/mwm/
  current.json                 # atomic pointer (written last)
  {version}/                   # e.g. 261105
    catalog.json
    attribution.json
    ATTRIBUTION.md
    checksums.sha256
    discovery.json             # optional; mirrors server discovery metadata
    {Leaf}.navi-sidecar.v1.zst
```

Examples (scratch host paths map the same URL shapes on live):

| Resource | Example |
|----------|---------|
| Current catalog | `GET …/published/mwm/current.json` |
| Versioned catalog | `GET …/published/mwm/261105/catalog.json` |
| Sidecar | `GET …/published/mwm/261105/Norway_Hedmark.navi-sidecar.v1.zst` |
| Attribution JSON | `GET …/published/mwm/261105/attribution.json` |
| Human attribution | `GET …/published/mwm/261105/ATTRIBUTION.md` |

Relative `sidecar_url` in the catalog is under the version directory
(e.g. `261105/Norway_Hedmark.navi-sidecar.v1.zst`). Resolve against
`…/published/mwm/`.

---

## 3. Catalog schema `navi-mwm-catalog/v2`

Normative example (subset): [examples/catalog-v2.example.json](examples/catalog-v2.example.json).
CI validates that example against the field rules below.

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
| `region_to_leaves` | object | Navi region id → list of CoMaps leaf ids |
| `shared_leaves` | object | Leaf id → list of Navi regions that share it |
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
| `sidecar_url` | string | Relative URL under `published/mwm/` |
| `sidecar_bytes` | number | Sidecar size |
| `sidecar_sha256` | string | SHA-256 of sidecar bytes |
| `comaps_file` | string | `{id}.mwm` |
| `comaps_bytes` | number | Expected MWM size from `countries.txt` |
| `comaps_sha1_base64` | string | CoMaps SHA-1 (base64) for the MWM |
| `mwm_bytes` | number | Fingerprint size of the MWM used at bake |
| `mwm_sha256` | string | Fingerprint SHA-256 of that MWM |
| `mwm_fingerprint` | object | `{size, sha256}` — same values; prefer for checks |
| `shared` | bool | True if the leaf appears in multiple Navi regions |
| `shared_with_regions` | string[] | Present when `shared` |
| `no_sidecar` | bool | Optional. `true` for non-routable CoMaps overlays (`World`, `WorldCoasts`) — clients must not expect a Navi sidecar |
| `sidecar` / `sidecar_url` | null | Set when `no_sidecar` is true |
| `elevation_policy_version` | number\|null | Sidecar elevation policy (`1` = uniform 2 m; `2` = Copernicus-only &lt;60°N uses 5 m). `null` when `no_sidecar` |

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
   User-Agent) → ordered mirror list (best first).
3. `GET {mirror}/maps/{series}/{V}/countries.txt` and
   `GET …/countries.txt.sig` (exactly **64** bytes).
4. Verify Ed25519:
   - Public key (hex): `91c0a9f6aa182371f047e256ab46489211acc2b51b13197fbe8c94eaa9749c7b`
     (CoMaps `COUNTRIES_TXT_SIGNATURE_HEX` / `private.h`; also in catalog
     `comaps.countries_txt_pubkey_hex`).
   - Message = raw `countries.txt` bytes; signature = raw `.sig` bytes.
5. Parse leaf `id`, `size`, `sha1_base64` from `countries.txt`.
6. Download `GET {mirror}/maps/{series}/{V}/{UrlEncode(id)}.mwm`.
   - URL-encode like CoMaps (`quote` with safe `-_.`); spaces become `%20`.
7. Verify **size** and **SHA-1 (base64)** against `countries.txt` / catalog.

### Polite-client rules

| Rule | Guidance |
|------|----------|
| User-Agent | Identify the app + contact; e.g. `Navi/… (+https://…; contact=…)` |
| Concurrency | ≤ 2 MWM downloads at once (navi-server uses 2) |
| Resume | HTTP Range / `.part` files; retry on transient errors |
| Backoff | Exponential backoff with jitter on 429/5xx |
| Failover | Try next mirror from `/servers` on hard failure |

---

## 5. Navi region → CoMaps leaf-set

Source of truth on the server:
`config/mwm/catalog/region_leaf_mapping.json`.

Published as:

- `region_to_leaves[region_id] = ["LeafA", "LeafB", …]`
- `shared_leaves[Leaf] = [region_id, …]`

### UI presentation

- Show the **Navi region** name the user already knows (e.g. Østlandet).
- Download the **union** of CoMaps leaves for that region.
- If a leaf is `shared`, download it once; reuse across regions.
- Coverage notes in the mapping file explain ocean/fjord gaps; for
  cross-border routing, include neighbour leaves the route needs (same
  release).

### Migration from `.rkyv` region IDs

Keep the same Navi region string ids (e.g. `europe_norway_ostlandet`). The
mapping file lists which CoMaps leaves replace the old single `.rkyv` pack for
that region. Until MWM is enabled for a region, continue serving `.rkyv` from
`published/current.json`. See [client-migration-from-rkyv.md](client-migration-from-rkyv.md).

---

## 6. Release switching

1. Read new `current.json` (or poll `comaps.release_version`).
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

## 10. Fetch order (one region)

1. `GET …/mwm/current.json`
2. `leaves = region_to_leaves[region_id]`
3. Discover CoMaps mirrors; download each `{leaf}.mwm`; verify size + SHA-1
4. Download each sidecar; verify SHA-256
5. Enforce fingerprint (§7)
6. Apply wetland / boardwalk / elevation per [client-routing-hooks.md](client-routing-hooks.md)
