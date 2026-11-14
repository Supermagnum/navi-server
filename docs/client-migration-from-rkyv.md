# Client migration — `.rkyv` packs → CoMaps MWMs + Navi sidecars

Step-by-step plan for the Navi Android/client team. Server keeps publishing
`.rkyv` under `published/current.json` until you retire regions; MWM sidecars
live under `published/mwm/` with **`NAVI_MWM_ENABLED=0`** until operators flip
bake on.

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

- `GET published/mwm/current.json`; download sidecars; verify SHA-256.
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

## Step 8 — Retire `.rkyv` packs (server, later)

Server may stop baking a region’s `.rkyv` only when:

1. Client release with MWM path is shipped and adopted for that region.
2. MWM catalog covers the region’s leaf set for a current CoMaps release.
3. Operators confirm grace + rollback docs.

Until then, **do not** remove `.rkyv` publish/bake/scrub.

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
