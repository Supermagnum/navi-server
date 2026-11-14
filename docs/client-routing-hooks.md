# Client routing hooks — wetland, boardwalk, elevation

What the Navi client router must do with
`{leaf}.navi-sidecar.v1.zst` data. Pair with
[sidecar-format.md](sidecar-format.md) and
[client-fetch-mwm.md](client-fetch-mwm.md).

---

## 1. Wetland policy

| Class | Byte | Edge cost multiplier | OSM subtypes |
|-------|-----:|---------------------:|--------------|
| Soft | 1 | **×5** | bog, string_bog, fen, untyped wetland |
| Hard | 2 | **≈ forbid** (`1e6`) | swamp, reedbed |
| (omit) | — | no sidecar record | **marsh** and other subtypes |

Lookup key: `(feature_id, segment_index, direction)` from the CoMaps road
graph edge being costed. If a boardwalk flag exists for the same key,
**do not** apply Soft/Hard.

### Profiles

Apply Soft/Hard to **pedestrian / hiking / outdoor foot** profiles (the
validation hook uses CoMaps `pedestrian`). Do **not** apply wetland Soft/Hard
to car/truck/bicycle unless product explicitly decides otherwise.

---

## 2. Boardwalk exemption

Proxy (PBF-free, from MWM types ∩ wetland geometry):

`highway-{footway,path,track,pedestrian}-bridge` ∩ wetland → `boardwalk_flags`

Matching segments are exempt from wetland Soft/Hard.

**Known limitation:** OSM boardwalk recall ≈ **25%** / precision ≈ **18%** on
Østlandet (many OSM boardwalks lack `bridge=yes` or miss wetland triangles).
Ship as best-effort; do not block Soft/Hard on perfect boardwalk parity.

---

## 3. Elevation overrides

For each `elevation_overrides` feature:

1. Align with MWM geometry via `feature_id` and `point_count`.
2. For each sparse `(point_index, altitude_dm)`, replace the CoMaps altitude
   at that point with `altitude_dm / 10.0` metres. Treat `altitude_dm` as a
   signed integer decimetre (**i32**); body schema v2 (`EL02`) encodes
   zigzag-i32 deltas so values above ±3276.7 m are valid. Legacy v1 bodies
   used zigzag-i16 and omitted out-of-range absolutes at bake.
3. Points without overrides keep CoMaps altitudes.

Threshold at bake depends on **elevation policy version** (catalog +
attribution `navi.elevation_policy_version`):

- **v1** (`261105`): `|Δh| > 2.0 m` for all leaves
- **v2** (next CoMaps release default): Copernicus-only leaves below 60°N use
  `|Δh| > 5.0 m`; ≥60°N and other Mapterhorn classes keep 2.0 m

Zoom default **12**. Clients need no code change when the policy version
bumps — they still replace altitudes at `(feature_id, point_index)`.

**Drape** (terrain mesh) stays **client-side** (Mapterhorn / other DEM); the
sidecar only corrects **routable** altitudes used by the cost function /
altitude section.

---

## 4. Reference implementation (server validation)

Minimal CoMaps `routes_builder` edge-cost hook used to validate sidecars:

| File | Lines | Role |
|------|------:|------|
| `scripts/mwm/hook/navi_wetland_cost.cpp` | ~308 | Sectioned NVSC loader + Soft×5 / Hard×1e6 |
| `scripts/mwm/hook/navi_wetland_cost.hpp` | ~47 | API |
| `scripts/mwm/apply-wetland-hook.sh` | ~108 | Patch CoMaps tree, link `PRIVATE zstd`, rebuild |

Env: `NAVI_WETLAND_SIDECAR_DIR` = directory of
`{Leaf}.navi-sidecar.v1.zst` files (stem = MWM stem).

The hook loads the `wetland_edges` section (boardwalk handled at annotate by
omitting Soft/Hard on those keys in production JSONL; client should still
honour `boardwalk_flags` explicitly). Flat spike2 layout is a fallback only.

### Map onto Navi client (spike 3 decision)

| Layer | Approach |
|-------|----------|
| Routing core | CoMaps C++ core via **NDK/FFI** (not a second graph) |
| Wetland / boardwalk | Same cost multipliers in the edge-cost path (port or share the hook) |
| Elevation overrides | Apply when reading MWM altitudes / building the routing graph |
| Navi Rust + Kotlin | Orchestration, download, fingerprint, UI; call into FFI router |
| Drape | Client DEM; independent of sidecar |

---

## 5. Validation routes (self-test)

### Foot wetland detour (Hedmark / Østlandet)

- Vehicle: pedestrian  
- From: `61.0500, 11.4700` → To: `61.0800, 11.5200`  
- Expected length **without** Soft/Hard: **13867 m**  
- Expected length **with** sectioned hook: **16245 m** (+~2.4 km SoftAvoid detour)

### Six-route suite

Leafset: `config/mwm/leafsets/six-route.txt`.  
Runner: `scripts/mwm/run-six-route-check.sh` (also from
`validate-comaps-release.sh`).

| Name | From | To | Expect |
|------|------|----|--------|
| hamar_elverum | 60.7945, 11.0680 | 60.8825, 11.5623 | NoError |
| elverum_lillehammer | 60.8825, 11.5623 | 61.1150, 10.4660 | NoError |
| hamar_oslo | 60.7945, 11.0680 | 59.9139, 10.7522 | NoError |
| lillehammer_drammen | 61.1150, 10.4660 | 59.7440, 10.2045 | NoError |
| norway_sweden | 60.8825, 11.5623 | 60.6847, 13.7072 | NoError |
| sweden_finland | 65.8355, 24.1367 | 65.8480, 24.1466 | NoError |

Client acceptance: reproduce the foot detour lengths (± small float noise) and
complete the six-route suite without routing errors on release **261105**
MWMs + matching sidecars.
