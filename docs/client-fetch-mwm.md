# Client fetch contract — CoMaps MWMs + Navi sidecars

Companion to [client-fetch.md](client-fetch.md). The existing `.rkyv` pack
catalog (`published/current.json`) is **unchanged**. MWM sidecars publish under
a separate tree and catalog.

## Enablement

Server-side sidecar bake is **off by default** (`NAVI_MWM_ENABLED=0`). Default
mode is `NAVI_MWM_MODE=comaps-release` (use CoMaps’ official planet release as
the base map; navi-server builds only Navi sidecars). The Nordic generator path
remains in-repo behind `NAVI_MWM_MODE=generate` for testing.

Clients should treat missing `published/mwm/current.json` as “MWM not offered yet”.

## Layout (comaps-release)

```
published/mwm/
  current.json                          # written last (atomic promote)
  {version}/                            # CoMaps data version, e.g. 261105
    attribution.json
    ATTRIBUTION.md
    checksums.sha256
    catalog.json
    discovery.json                      # metaserver + countries.txt metadata
    {Leaf}.navi-sidecar.v1.zst          # Navi only — no .mwm files here
```

Clients download **`.mwm` files from CoMaps mirrors** (via CoMaps discovery),
and **sidecars + catalog + attribution from navi-server**.

## CoMaps discovery (client)

Mirror the CoMaps app:

1. `GET {metaserver}/meta/maps.json` → `map-series[S].latest` = `V`
2. `GET {metaserver}/servers` with `X-OM-DataVersion: V` → ordered mirror list
3. `GET {mirror}/maps/{S}/{V}/countries.txt` (+ `.sig`) — leaf size + `sha1_base64`
4. `GET {mirror}/maps/{S}/{V}/{UrlEncode(id)}.mwm` — verify size + SHA-1

Do **not** hard-code a mirror list; use the metaserver response (offline fallback
only). Credit CoMaps and mirror operators (see `ATTRIBUTION.md`).

## `current.json` (schema `navi-mwm-catalog/v2`)

| Field | Meaning |
|-------|---------|
| `schema` | `navi-mwm-catalog/v2` |
| `mode` | `comaps-release` |
| `version` | CoMaps data version directory name |
| `comaps.map_series` / `release_version` | CoMaps series + version |
| `comaps.countries_txt_sha256` | Checksum of the release `countries.txt` |
| `comaps.metaserver_url` | Metaserver used for discovery (no hard-coded mirrors) |
| `leaves[]` | Per leaf: CoMaps file name/size/`sha1_base64`; Navi `mwm_sha256`+`mwm_bytes` fingerprint; sidecar URL/size/SHA-256; `shared` |
| `region_to_leaves` | Navi region id → CoMaps leaf id list (Norway draft + growing) |
| `shared_leaves` | Leaf → Navi regions that share it |

Fetch order for a Navi region:

1. GET `…/mwm/current.json`
2. Resolve leaf ids via `region_to_leaves[region_id]`
3. Discover CoMaps mirrors; download each `{leaf}.mwm` from CoMaps; verify size + SHA-1 against catalog / `countries.txt`
4. Download `{leaf}.navi-sidecar.v1.zst` from navi-server; verify SHA-256
5. **Reject** any sidecar whose embedded MWM fingerprint (size + SHA-256) ≠ the local CoMaps MWM
6. Apply wetland Soft/Hard + boardwalk + elevation overrides from the sidecar

## Sidecar (`*.navi-sidecar.v1.zst`)

Server builds; Navi reads. Logical format (zstd-wrapped):

- Magic `NVSC`, format version `1`
- Generator tag + SHA (`comaps-release/{series}` + version)
- MWM size + SHA-256 fingerprint (of the exact CoMaps bytes)
- Section directory with per-section zstd + checksums

v1 sections:

| Section | Content |
|---------|---------|
| `wetland_edges` | `feature_id`, `segment_index`, `direction`, class Soft/Hard |
| `boardwalk_flags` | boardwalk carve-outs |
| `elevation_overrides` | sparse Mapterhorn altitudes (decimetres, delta-encoded) vs CoMaps |
| `attribution` | UTF-8 JSON (OSM, CoMaps+mirrors, Mapterhorn, Navi) |

Policy (matches Navi): Soft = bog / string_bog / fen / untyped wetland;
Hard = swamp / reedbed; **marsh omitted**.

Elevation: only points differing from the CoMaps `altitudes` section by more than
a configurable threshold (default 2 m) are stored.

## Retention

Whole-release generations. Keep current + previous for `NAVI_MWM_GRACE_DAYS`
(`NAVI_GRACE_DAYS`) after supersede. A release referenced by `current.json` is
never deleted. Bake/check holds `data/state/pack-pipeline.lock`.

## What Navi must implement

1. Optional MWM catalog fetch (parallel to `.rkyv` packs) — schema v2.
2. CoMaps metaserver discovery for MWMs; navi-server for sidecars + attribution.
3. Bind sidecar by fingerprint before applying wetland / elevation; never apply a
   mismatched sidecar.
4. Wire Soft/Hard multipliers / boardwalk carve-out / elevation overrides (client
   routing — not in navi-server).
5. Show offline attribution from `ATTRIBUTION.md` / `attribution.json`.
