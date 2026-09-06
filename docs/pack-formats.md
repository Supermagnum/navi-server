# Pack file formats

Binary and JSON formats produced by `navi-indexed-convert` and published under
`data/published/`. Source of truth for the binary layouts is the in-repo crate
`pack_convert_core::routing::indexed` (originally extracted from Navi
`driver_break_core::routing::indexed`).

HTTP fetch contract (URLs, digests, exposure surface): [client-fetch.md](client-fetch.md).

---

## Published tree

Layout under `packs/` mirrors **Geofabrik download paths** (the same
slash-separated strings the Navi app’s Download-scope picker uses), not the
flat underscore bake ids used in convert scratch.

```text
data/published/
  current.json
  packs/<geofabrik-path>/<generation>/
    manifest.json                 # client digests + pointers
    checksums.sha256              # sha256sum text
    <stem>.navi-manifest.json     # convert manifest (same as on-device)
    <stem>.navi-graph-<profile>.rkyv              # monolithic graph
    <stem>.navi-graph-<profile>.t{row}_{col}.rkyv # tiled graph
    <stem>.navi-poi-barrier.rkyv                  # always monolithic
    <stem>.navi-wetland.rkyv                      # monolithic wetland
    <stem>.navi-wetland.t{row}_{col}.rkyv         # tiled wetland
```

Example: bake id `asia_china_anhui` (source `geofabrik:asia/china/anhui`) publishes to
`packs/asia/china/anhui/<generation>/`.
| File | Kind | Role |
|---|---|---|
| `current.json` | JSON | Live generation index for all regions |
| `manifest.json` | JSON | Per-region publish manifest (sha256 + bytes per file) |
| `checksums.sha256` | text | Same digests, `sha256sum` line format |
| `*.navi-manifest.json` | JSON | Convert inventory (profiles, tiles, format versions, Δh) |
| `*.navi-graph-*.rkyv` | binary | Routing graph (nodes, edges, optional elevation Δh) |
| `*.navi-poi-barrier.rkyv` | binary | POIs, danger-barrier segments, glaciers, overnight buildings |
| `*.navi-wetland*.rkyv` | binary | SoftAvoid / HardAvoid wetland rings |

Profile keys: `car`, `truck`, `foot`, `bicycle`.

---

## Common binary archive layout

Every `.rkyv` pack is:

```text
[0..4]  magic          u32 little-endian
[4..8]  format_version u32 little-endian
[8.. ]  rkyv 0.8 payload for that exact (magic, version)
```

| Pack | Magic (u32) | ASCII | On-disk bytes 0..4 (LE) | Version |
|---|---|---|---|---|
| Graph | `0x4E56524B` | `NVRK` | `4B 52 56 4E` | **6** |
| POI + barrier | `0x4E565042` | `NVPB` | `42 50 56 4E` | **2** |
| Wetland | `0x4E56574C` | `NVWL` | `4C 57 56 4E` | **1** |

Rules:

- Payload offset is always **8** bytes.
- A mismatched magic or version must be rejected (no reinterpretation).
- Writes are atomic: `{path}.partial` → fsync → rename. Never mmap `.partial`.
- Serialization is **rkyv 0.8**. There is no Python decoder for pack bodies.

Quick preamble check (shell):

```bash
xxd -l 8 path/to/file.rkyv
# expect e.g. graph: 4b 52 56 4e 06 00 00 00
```

---

## Graph pack (`FlatGraphPack`)

**Files:** `{stem}.navi-graph-{profile}.rkyv` or
`{stem}.navi-graph-{profile}.t{row}_{col}.rkyv`

**Constants:** `MAGIC_GRAPH`, `GRAPH_FORMAT_VERSION = 6`  
**Source:** `pack-convert-core/src/routing/indexed/graph_pack.rs`

### Contents

Parallel column-store vectors (node count / edge count aligned):

| Field | Type | Meaning |
|---|---|---|
| `has_delta_h` | `bool` | Whether elevation deltas were baked |
| `node_ids` | `Vec<i64>` | OSM node ids |
| `node_lats` / `node_lons` | `Vec<f64>` | Node coordinates (WGS84) |
| `edge_src` / `edge_tgt` | `Vec<u32>` | Indices into `node_ids` |
| `edge_length_m` | `Vec<f64>` | Edge length (metres) |
| `edge_base_weight` | `Vec<f64>` | Profile routing weight |
| `edge_delta_h_m` | `Vec<f32>` | Elevation change end−start (m); **empty** if `has_delta_h` is false; **NaN** = missing DEM sample |
| `edge_start_*` / `edge_end_*` | `Vec<f64>` | Edge endpoint lat/lon |
| `edge_highway` | `Vec<String>` | OSM highway class |
| `edge_maxspeed_kmh` | `Vec<f64>` | Maxspeed; **NaN** = unset |
| `edge_name` / `edge_road_ref` | `Vec<String>` | Empty string = absent |
| `edge_is_motorroad` / `expressway` / `oneway` / `toll` / `ferry` / `roundabout` / `boardwalk` | `Vec<u8>` | `0`/`1` flags |
| `edge_lanes` | `Vec<u8>` | `0` = unset |
| `edge_maxweight_t` / `maxaxleload_t` / `maxbogieweight_t` | `Vec<f64>` | Tonnes; **NaN** = unset |
| `edge_maxheight_m` / `maxwidth_m` / `maxlength_m` | `Vec<f64>` | Metres; **NaN** = unset |
| `edge_shape_offsets` | `Vec<u32>` | CSR prefix sums; length = edges + 1 |
| `edge_shape_lons` / `edge_shape_lats` | `Vec<f64>` | Intermediate shape points (endpoints excluded) |
| `edge_*_conditional` | `Vec<String>` | Raw OSM conditional tags |
| `edge_access_forbidden` | `Vec<u8>` | Profile-static forbid |
| `node_access_blocked` | `Vec<u8>` | Barrier nodes |

Shape CSR: edge `i` uses indices `[edge_shape_offsets[i], edge_shape_offsets[i+1])`
into the lon/lat arrays. OSM way ids are **not** stored in v6.

### Elevation Δh

When convert runs with `--elev-dir` (server: `NAVI_BAKE_DELTA_H=1`):

- `has_delta_h = true`
- `edge_delta_h_m[i] = elev(end) − elev(start)` (metres, `f32`) when both
  endpoints sample successfully
- Missing DEM at either endpoint → **`f32::NAN`** (same NaN-as-unset
  convention as `edge_maxspeed_kmh` / vehicle limits). A true flat edge is
  still `0.0`. Readers must treat non-finite Δh as “no elevation data” (e.g.
  fall back to flat-energy), never as climb/descent.
- Manifest may include `delta_h_missing_edges` (count of NaN Δh entries across
  written graph packs; omitted when zero)

When elevation is off: `has_delta_h = false` and `edge_delta_h_m` is empty.

The Navi manifest mirrors this with `has_delta_h`, optional `elev_dir`, and
optional `delta_h_missing_edges`.

Ocean-only Copernicus cells are not published by the DEM provider; those
prefetch 404s use the same NaN sentinel if an edge endpoint ever lands there
(no separate ocean encoding).

### Tiling

A region tiles when lat span > 1.25°, lon span > 1.25°, or area > 2.0 deg².
Tiles are ~1° cells. Manifest `graph_tiles[profile]` lists `{ file, bbox }` with
bbox `[min_lat, min_lon, max_lat, max_lon]` (logical, no build pad).

---

## POI + barrier pack (`FlatPoiBarrierPack`)

**File:** `{stem}.navi-poi-barrier.rkyv` (always monolithic)

**Constants:** `MAGIC_POI_BARRIER`, `POI_BARRIER_FORMAT_VERSION = 2`  
**Source:** `pack-convert-core/src/routing/indexed/poi_barrier_pack.rs`

| Field | Type | Meaning |
|---|---|---|
| `osm_ids` / `lats` / `lons` | parallel | POI locations |
| `cat_masks` | `Vec<u16>` | Category bitfield (see below) |
| `icon_keys` / `names` | `Vec<String>` | Display metadata |
| `tag_offsets` / `tag_keys` / `tag_vals` | CSR | Extra OSM tags per POI |
| `seg_a_lon/lat` / `seg_b_lon/lat` | parallel | Danger-barrier segments (A→B) |
| `glacier_offsets` / `glacier_lon` / `glacier_lat` | CSR | Glacier polygons `[lon,lat]` |
| `building_lats` / `building_lons` | parallel | Overnight building centroids (v2+) |

### Category bits (`u16`)

| Bit | Category |
|---|---|
| `1 << 0` | Water |
| `1 << 1` | Cabin |
| `1 << 2` | General |
| `1 << 3` | NetworkHut |
| `1 << 4` | Restroom |
| `1 << 5` | OvernightFacility |
| `1 << 6` | CraftBrewery |
| `1 << 7` | TentSite |
| `1 << 8` | Fishing |
| `1 << 9` | RestArea |
| `1 << 10` | Lodging |

---

## Wetland pack (`FlatWetlandPack`)

**Files:** `{stem}.navi-wetland.rkyv` or `{stem}.navi-wetland.t{row}_{col}.rkyv`

**Constants:** `MAGIC_WETLAND`, `WETLAND_FORMAT_VERSION = 1`  
**Source:** `pack-convert-core/src/routing/indexed/wetland_pack.rs`

| Field | Type | Meaning |
|---|---|---|
| `ring_offsets` | `Vec<u32>` | Prefix sums into ring coords (length = rings + 1) |
| `ring_lon` / `ring_lat` | `Vec<f64>` | Ring vertices as `[lon, lat]` |
| `ring_class` | `Vec<u8>` | `1` SoftAvoid, `2` HardAvoid |

Boardwalk carve-outs live on graph edges (`edge_is_boardwalk`), not in this pack.
Empty wetland tiles are omitted (no zero-ring files).

---

## JSON manifests

### `{stem}.navi-manifest.json` (convert)

Schema **1**. Written by convert; identical shape on-device and on the server.

```json
{
  "schema": 1,
  "stem": "europe_france_alsace-latest",
  "pbf_filename": "europe_france_alsace-latest.osm.pbf",
  "pbf_size_bytes": 123456789,
  "pbf_modified_unix_secs": 1756987200,
  "graph_files": { "car": "….navi-graph-car.rkyv", "foot": "…" },
  "graph_tiles": {
    "car": [{ "file": "….t0_0.rkyv", "bbox": [47.0, 6.8, 48.0, 7.8] }]
  },
  "graph_format_version": 6,
  "poi_barrier_file": "….navi-poi-barrier.rkyv",
  "poi_barrier_format_version": 2,
  "wetland_file": "….navi-wetland.rkyv",
  "wetland_tiles": [],
  "wetland_format_version": 1,
  "has_delta_h": true,
  "delta_h_missing_edges": 0,
  "elev_dir": "/media/navi/navi-server/data/elevation"
}
```

Prefer `graph_tiles` / `wetland_tiles` when non-empty over the monolithic
`graph_files` / `wetland_file` entries.

### `manifest.json` (publish / client)

```json
{
  "schema": 1,
  "generation": "20260904T104909Z",
  "region_id": "europe/france/alsace",
  "bake_id": "europe_france_alsace",
  "stem": "europe_france_alsace-latest",
  "has_delta_h": true,
  "navi_manifest": "europe_france_alsace-latest.navi-manifest.json",
  "files": {
    "europe_france_alsace-latest.navi-graph-car.t0_0.rkyv": {
      "sha256": "<hex>",
      "bytes": 15290156
    }
  }
}
```

### `checksums.sha256`

```text
<hex>  <filename>
```

Two spaces between digest and name (`sha256sum` compatible). Digests cover the
full file (preamble + payload).

### `current.json`

```json
{
  "schema": 1,
  "generation": "20260904T104909Z",
  "created_unix": 1756987200,
  "packs_base": "/packs",
  "layout": "geofabrik-path",
  "regions": [
    {
      "region_id": "europe/france/alsace",
      "bake_id": "europe_france_alsace",
      "generation": "20260904T104909Z",
      "manifest_url": "/packs/europe/france/alsace/20260904T104909Z/manifest.json",
      "has_delta_h": true,
      "bytes": 682000000
    }
  ]
}
```

Updated atomically via `current.json.partial` → rename.

---

## How to read them

### Rust (supported)

Module: `pack_convert_core::routing::indexed` (in-repo).

| API | Returns |
|---|---|
| `load_graph_pack(path, profile)` | `RouteGraph` |
| `load_graph_pack_bbox(path, profile, bbox)` | `RouteGraph` clipped to bbox |
| `load_poi_barrier_pack(path)` | `(PoiIndex, DangerBarrierIndex)` |
| `load_wetland_pack(path, bbox)` | `WetlandIndex` |
| `try_load_graph_for_plan(_bbox)` | Manifest-aware load (Ready / Stale / Version) |
| `try_load_poi_barrier_for_plan` | Same gating |
| `try_load_wetland_for_plan` | Mono or tiled merge |
| `NaviManifest::load` | Parse `{stem}.navi-manifest.json` |
| `merge_tile_graphs` | Dedup boundary edges across tiles |

Typical load path:

```rust
// mmap file → check 8-byte preamble → rkyv::access → deserialize → materialize
let mmap = map_file(path)?;
check_preamble(&mmap, MAGIC_GRAPH, GRAPH_FORMAT_VERSION)?;
let body = &mmap[8..];
let archived = rkyv::access::<ArchivedFlatGraphPack, _>(body)?;
let pack: FlatGraphPack = rkyv::deserialize(archived)?;
let graph = pack.to_route_graph_bbox(profile, bbox);
```

To inspect raw `edge_delta_h_m`, deserialize to `FlatGraphPack` yourself (the
`RouteGraph` adapter used by planners may not copy Δh onto every edge object).

### CLIs

| Tool | Purpose |
|---|---|
| `navi-indexed-convert` | PBF (+ optional DEM) → packs + `.navi-manifest.json` |
| `scripts/validate-packs.sh` | Presence, size bands (global `NAVI_SIZE_*` plus optional per-region `*_min_ratio` / `*_max_ratio` or `terrain_class=polar_sparse` / `wetland_heavy` on `regions.conf` lines), weak ≥64B header check (does **not** verify magic/version) |
| `scripts/publish-packs.sh` | Staging → published under `packs/<geofabrik-path>/` + `manifest.json` / checksums / `current.json` |
| `scripts/lib/publish-safe.sh` | Single-region publish used by planet smoke / batched leaves |
| `scripts/lib/published_tree.py` | Nested packs tree walk + `current.json` rebuild |
| `scripts/migrate-published-to-geofabrik-paths.sh` | Move legacy flat `packs/<bake_id>/` → Geofabrik paths |

**Size bands and `terrain_class`.** Global `NAVI_SIZE_*` ratios gate pack/PBF
size sanity. Per-region trailing `key=value` on `regions.conf` lines can
narrow or widen one band for one region (e.g. `wetland_max_ratio=1.0`).
`terrain_class=polar_sparse` sets only `graph_min` to
`NAVI_SIZE_GRAPH_MIN_RATIO_POLAR_SPARSE` (default `0.001`).
`terrain_class=wetland_heavy` sets only `wetland_max` to
`NAVI_SIZE_WETLAND_MAX_RATIO_WETLAND_HEAVY` (default `1.0`) for mire /
mangrove / coastal-marsh / major-delta extracts (anchors: Hedmark 0.786,
Guinea-Bissau 0.643, Florida 0.576). Untagged regions keep global floors/
ceilings. “Wet climate” alone is not enough — Brazil Norte (Amazon) measured
wetland ratio 0.054 because rainforest is mostly non-wetland OSM tags. Keep
tags in `data/regions.conf` (merged when baking from `regions.planet.conf`);
do not hand-edit the auto-generated planet leaf list for overrides.

**`skip_reason=no_road_network`.** Orchestration exclusion for leaves with
**zero** `highway=*` ways (convert hard-fails empty graphs by design).
Not a size-band override and not `terrain_class` — those apply when some
routable data exists but fails a heuristic. Keep the tag on
`data/regions.conf`; `run-planet-leaves-batched.sh` skips
fetch/convert/validate/publish before work starts, logs the reason, and
still counts the leaf toward batch progress. Confirm 0 highways with a PBF
probe before tagging.

Build convert (in-repo `pack-convert-core` / `navi-indexed-convert`):

```bash
cd /media/navi/navi-server
cargo build --release -p navi-indexed-convert
```

### Python / other languages

JSON manifests and `checksums.sha256` are plain text — any language can parse
them. **There is no Python rkyv pack decoder.** Cross-language consumers must
link Rust (or reimplement rkyv 0.8 for these exact structs and versions).

Verify digests after download:

```bash
cd packs/<geofabrik-path>/<generation>
sha256sum -c checksums.sha256
# or compare against manifest.json "files.*.sha256"
```

---

## Versioning policy

- Magic + `format_version` identify an exact payload schema.
- Bumping a version requires a new convert build and matching loaders; old packs
  must fail closed (`VersionMismatch`).
- Graph history note: v5 lacked vehicle physical-limit vectors; **v6** added them.
- POI/barrier v2 added overnight building centroids.

---

## Source map

| Topic | Path |
|---|---|
| Preamble | `pack-convert-core/src/routing/indexed/header.rs` |
| Graph body | `pack-convert-core/src/routing/indexed/graph_pack.rs` |
| POI/barrier body | `pack-convert-core/src/routing/indexed/poi_barrier_pack.rs` |
| Wetland body | `pack-convert-core/src/routing/indexed/wetland_pack.rs` |
| Loaders | `pack-convert-core/src/routing/indexed/load.rs` |
| Manifest + filenames | `pack-convert-core/src/routing/indexed/manifest.rs` |
| Convert / tiling | `pack-convert-core/src/routing/indexed/convert.rs` |
| Atomic IO | `pack-convert-core/src/routing/indexed/io.rs` |
| HTTP URLs | [client-fetch.md](client-fetch.md) |
| Publish script | `scripts/publish-packs.sh` |
| Nested catalog helper | `scripts/lib/published_tree.py` |
| Flat→nested migrate | `scripts/migrate-published-to-geofabrik-paths.sh` |
