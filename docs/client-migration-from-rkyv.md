# Client migration — `.rkyv` packs → CoMaps MWMs + Navi sidecars

Step-by-step plan for the Navi Android/client team. Server keeps **serving**
frozen `.rkyv` under `published/current.json` until you retire regions; MWM
sidecars live under filesystem `published/mwm/` and are served as `/mwm/`
(weekly `.rkyv` bake is frozen; MWM bake
stays gated by **`NAVI_MWM_ENABLED=0`** until operators flip it on).

**Server footprint (why cutover matters):** published `.rkyv` packs are on the
order of **~1.7 TiB** logical (~656 GiB compressed on the live pool); the
published MWM sidecar tree for a full world release is about **~2.1 GiB**.
Clients still download CoMaps leaf `.mwm` files from the CoMaps CDN separately.
See [mwm-pipeline.md](mwm-pipeline.md#disk-rkyv-old-vs-mwm-sidecars-new).

---

## Step 0 — Read the contracts

| Doc | Use |
|-----|-----|
| [client-fetch-mwm.md](client-fetch-mwm.md) | Catalog, CoMaps discovery, fingerprints |
| [sidecar-format.md](sidecar-format.md) | NVSC bytes |
| [client-routing-hooks.md](client-routing-hooks.md) | Soft/Hard, boardwalk, elevation |
| [client-fetch.md](client-fetch.md) | Existing `.rkyv` HTTP contract (unchanged) |

**Acceptance:** engineer can implement download + verify without opening
`scripts/mwm/`.

---

## Step 1 — Keep existing `.rkyv` path

- Keep Geofabrik / server `.rkyv` download, validate, and router for all
  regions still marked `.rkyv`-only.
- Keep offline attribution for packs.

**Acceptance:** no regression on current production regions.

---

## Step 2 — Add CoMaps MWM fetch

- Implement metaserver → maps.json → `/servers` → `countries.txt` + Ed25519.
- Download leaves for one pilot region (e.g. `europe_norway_ostlandet` via
  `region_to_leaves`).
- Verify size + SHA-1; polite User-Agent; concurrency ≤ 2; resume + backoff.

**Acceptance:** MWMs for Østlandet leaf set verify against `countries.txt` for
release `261105` (or current catalog version).

---

## Step 3 — Add sidecar fetch + fingerprint

- `GET /mwm/current.json` (public base
  `https://navigate-me.duckdns.org/mwm/`); download sidecars; verify SHA-256.
- Enforce MWM size + SHA-256 fingerprint before applying layers.
- On mismatch / missing sidecar: route on MWM only; warn “Navi layers off”.

**Acceptance:** Hedmark inspect fingerprint matches local MWM; deliberate
corrupt MWM refuses the sidecar.

---

## Step 4 — Wire wetland Soft/Hard (+ boardwalk)

- Load `wetland_edges` / `boardwalk_flags` into the CoMaps edge-cost path
  (FFI), Soft×5 / Hard≈forbid, boardwalk exempt.
- Reproduce foot detour **13867 → 16245 m** and six-route NoError.

**Acceptance:** lengths and suite match [client-routing-hooks.md](client-routing-hooks.md).

---

## Step 5 — Wire elevation overrides

- Apply `elevation_overrides` to routable altitudes.
- Keep drape on client DEM.

**Acceptance:** overridden points differ from stock CoMaps altitudes; non-override
points unchanged.

---

## Step 6 — Region UI + shared leaves

- Present **Navi region** names; download the CoMaps leaf union.
- Dedupe `shared` leaves across regions.
- Atomic release switch; keep previous release for grace (≥ 7 days server-side).

**Acceptance:** switching release never mixes leaf versions; failed download
leaves previous release active.

---

## Step 7 — Run both formats side by side

| Region state | Client behaviour |
|--------------|------------------|
| Only in `.rkyv` catalog | `.rkyv` path |
| In MWM catalog + feature flag | Prefer MWM + sidecar |
| Sidecar missing / fingerprint fail | MWM without Navi layers + warning |
| User / kill-switch | Fall back to `.rkyv` if still published |

**Acceptance:** flag off → identical to today; flag on for Østlandet → MWM path.

---

## Step 8 — Retire `.rkyv` packs (server, later — deliberate)

Frozen `.rkyv` packs stay **served** until the new Navi client has been tested
and works correctly on MWM + sidecars. Freeze (timers off +
`published/.navi-rkyv-frozen`) is **not** retirement.

Only after client adoption, operators retire `.rkyv` as a **separate** step:

1. Confirm MWM catalog `country_groups` + `region_to_leaves` cover needed areas
   for a current CoMaps release; client release shipped and validated.
2. **Stop serving** `.rkyv`: remove/disable the HTTP aliases that expose
   `published/packs/` and `published/current.json` region manifests (keep a
   rollback snapshot).
3. **Remove the `.rkyv` tree** under `published/packs/` (and obsolete manifests)
   after the grace window; free disk (~1.7 TiB logical / ~656 GiB ZFS today).
4. **Remove old bake code / timers** (`navi-pack-bake`, scrub, convert-region
   paths) from the live host once no rollback is required.
5. Document the cutover in the operator log; keep MWM publish + CoMaps discovery
   as the sole map path.

Until steps 1–2, **do not** delete published `.rkyv` packs.

---

## Open client-side decisions

| Issue | Notes |
|-------|-------|
| Android KVM / emulator test | Spike 3: NDK build + zstd vendoring; confirm CI on KVM |
| Fastest vs shortest | Soft×5 changes “best” foot routes; product copy / toggle |
| Boardwalk recall ~25% | Accept proxy vs optional PBF enrichment later |
| Wetland on bike profiles? | Default: foot/hike only |
| Drape DEM source | Independent of sidecar; pick Mapterhorn or other |

---

## What to remove (only after Step 8)

- Per-region `.rkyv` download UI once MWM is sole path for that region.
- Duplicate wetland geometry packs if Soft/Hard fully comes from sidecars.
- Do **not** remove server `.rkyv` pipeline from this repo as part of client work.
