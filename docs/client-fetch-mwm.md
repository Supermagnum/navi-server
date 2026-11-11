# Client fetch contract — MWM + Navi sidecars

Companion to [client-fetch.md](client-fetch.md). The existing `.rkyv` pack
catalog (`published/current.json`) is **unchanged**. MWM publishes under a
separate tree and catalog.

## Enablement

Server-side generation is **off by default** (`NAVI_MWM_ENABLED=0`). Enabling
is an operator step (see `/tmp/navi-mwm-apply.sh` on the bake host). Clients
should treat missing `published/mwm/current.json` as “MWM not offered yet”.

## Layout

```
published/mwm/
  current.json                          # written last (atomic promote)
  {version}/
    attribution.json                    # Mapterhorn sources (never generic)
    checksums.sha256
    catalog.json                        # same body as current when live
    {Leaf}.mwm
    {Leaf}.navi-sidecar.v1.zst
```

`{version}` is the CoMaps maps version directory name from a **single**
generator run (e.g. `261111`). Never mix MWMs from different runs in one
version directory.

## `current.json` (schema `navi-mwm-catalog/v1`)

Fields:

| Field | Meaning |
|-------|---------|
| `version` | Release id (= directory name under `published/mwm/`) |
| `generator.tag` / `generator.sha` | Pinned CoMaps tag + commit |
| `limitation` | Pilot: cross-border routing **out of** the Nordic set unsupported |
| `leaves[]` | Per leaf: `id`, `mwm_bytes`, `mwm_sha256`, `sidecar_sha256`, `sidecar_bytes`, `shared` |
| `region_to_leaves` | Navi region id → CoMaps leaf id list |
| `shared_leaves` | Leaf → Navi regions that share it (e.g. `Norway_Southern`) |

Fetch order for a Navi region:

1. GET `…/mwm/current.json`
2. Resolve leaf ids via `region_to_leaves[region_id]`
3. Download each `{leaf}.mwm` + `{leaf}.navi-sidecar.v1.zst` from `…/mwm/{version}/`
4. Verify SHA-256 against catalog / `checksums.sha256`
5. Reject sidecars whose embedded MWM fingerprint ≠ local MWM

## Sidecar (`*.navi-sidecar.v1.zst`)

Server builds; Navi reads. Logical format (zstd-wrapped):

- Magic `NVSC`, format version `1`
- Generator tag + SHA
- MWM size + SHA-256 fingerprint
- Section directory with per-section zstd + checksums

v1 sections:

| Section | Content |
|---------|---------|
| `wetland_edges` | `feature_id`, `segment_index`, `direction`, class Soft/Hard |
| `boardwalk_flags` | boardwalk carve-outs |
| `attribution` | UTF-8 JSON (Mapterhorn sources) |

Policy (matches Navi): Soft = bog / string_bog / fen / untyped wetland;
Hard = swamp / reedbed; **marsh omitted**.

## Pilot leaf set

Nordic: Norway, Sweden, Denmark, Finland, Svalbard, Jan Mayen — one consistent
release. Cross-MWM routing works **inside** that set. Routing that needs
neighbours outside the set (Germany, Russia, …) is unsupported in the pilot.

## Retention

Whole-release generations. Keep current + previous for `NAVI_MWM_GRACE_DAYS`
after supersede. A release referenced by `current.json` is never deleted.

## What Navi must implement

1. Optional MWM catalog fetch (parallel to `.rkyv` packs).
2. Download + verify MWM + sidecar for mapped leaves (dedupe `shared` leaves).
3. Bind sidecar by fingerprint before applying wetland edge costs.
4. Wire Soft/Hard multipliers / boardwalk carve-out (client routing hook — not
   in navi-server).
5. Surface the pilot limitation when a route exits the Nordic leaf set.
