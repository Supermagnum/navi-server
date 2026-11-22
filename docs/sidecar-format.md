# Sidecar format — `{leaf}.navi-sidecar.v1.zst`

Byte-level spec for the Navi CoMaps sidecar. Implemented by
`crates/navi-mwm-sidecar`. Inspect with:

```bash
navi-mwm-sidecar inspect --sidecar Norway_Hedmark.navi-sidecar.v1.zst
# optional fingerprint check against a local MWM:
navi-mwm-sidecar inspect --sidecar … --mwm Norway_Hedmark.mwm
```

Endianness: **little-endian** for all multi-byte integers.  
Outer file: **one zstd frame** (compress level 10) around the logical blob.

---

## 1. Logical blob layout

```
magic              4 bytes   "NVSC"
format_version     u32       = 1
generator_tag      u16-len + UTF-8
generator_sha      u16-len + UTF-8
mwm_sha256         32 bytes
mwm_size           u64
section_count      u32
section_directory  section_count × entry
payload            concatenated per-section zstd frames
```

`u16-len` string: `u16` byte length, then that many UTF-8 bytes (no NUL).

### Section directory entry

| Field | Type | Meaning |
|-------|------|---------|
| `name` | u16-len string | Section id |
| `uncompressed_size` | u64 | Bytes after inner zstd decompress |
| `compressed_size` | u64 | Inner zstd frame length |
| `sha256` | 32 bytes | SHA-256 of **uncompressed** section body |
| `offset` | u64 | Offset from start of **payload** (not file) |

Section bodies are independently zstd-compressed. Verify each section’s
SHA-256 after decompress before use.

### Known sections (v1)

Directory order written by `navi-mwm-sidecar` bake:

| Order | Name | Role | Required for Navi wetland routing |
|------:|------|------|-----------------------------------|
| 1 | `wetland_edges` | Soft/Hard edge records | **yes** |
| 2 | `boardwalk_flags` | Carve-out flags | no (empty OK) |
| 3 | `elevation_overrides` | Point altitude overrides | no (empty OK; drape still works) |
| 4 | `vehicle_limits` | Per-segment height/width/length/weight caps | no — **Option** (§ below) |
| 5 | `named_routes` | Named OSM route search stubs | no — **Option** (§ below) |
| last | `attribution` | UTF-8 JSON credits | **yes** for offline display |

`vehicle_limits` and `named_routes` appear only when the bake supplies them
(`Some(_)`). Older published leaves (e.g. Hedmark fixture in §10) may omit both.

### Option vs empty-section semantics

For `vehicle_limits` and `named_routes` only:

| Bake input | Wire result | Client meaning |
|------------|-------------|----------------|
| `None` (flag / env PBF unset) | **Section omitted** from the directory | Feature not annotated for this leaf (“not available”) |
| `Some([])` | Section **present**, body magic + `count = 0` | Annotated; nothing matched |
| `Some([…])` | Section present with records | Use the data |

Wetland, boardwalk, and elevation are always emitted (possibly with
`count = 0` / empty feature list). Old clients that do not know an optional
section name **skip** it (reader rule 2) — they must not reject the sidecar.

### Reader rules

1. Reject bad magic, unsupported `format_version`, truncated headers, size or
   checksum mismatches.
2. **Unknown section names:** treat as **optional** — skip (do not fail).
3. **Unknown required behaviour:** if `format_version` is greater than the
   client supports, **reject** the whole sidecar (prompt app update).
4. Never apply costs unless the MWM fingerprint matches (§ fingerprint in
   [client-fetch-mwm.md](client-fetch-mwm.md)).

---

## 2. `wetland_edges` body

```
count     u32
records   count × 8 bytes
```

Each record:

| Offset | Type | Field |
|-------:|------|-------|
| 0 | u32 | `feature_id` |
| 4 | u16 | `segment_index` |
| 6 | u8 | `direction` (0 or 1) |
| 7 | u8 | `class`: `1` = Soft, `2` = Hard |

Sort order (ascending): `(feature_id, segment_index, direction, class)`.
Duplicates dropped at bake. Lookups are exact key match on
`(feature_id, segment_index, direction)`.

Class policy at annotate time: Soft = bog / string_bog / fen / untyped
wetland; Hard = swamp / reedbed; **marsh omitted**.

---

## 3. `boardwalk_flags` body

Same 8-byte record shape as wetland:

| Offset | Type | Field |
|-------:|------|-------|
| 0 | u32 | `feature_id` |
| 4 | u16 | `segment_index` |
| 6 | u8 | `direction` |
| 7 | u8 | `flag` (loaders write `1`) |

Proxy: `highway-{footway,path,track,pedestrian}-bridge` ∩ wetland geometry.
Matching segments are **exempt** from Soft/Hard penalties. OSM boardwalk
recall of this proxy is ~25% (see routing-hooks).

---

## 4. `elevation_overrides` body

Integer **decimetres** (0.1 m). Only points where
`|Mapterhorn − CoMaps| > threshold` are stored.

**Elevation policy version** (also in catalog + `attribution.json` →
`navi.elevation_policy_version`):

| Version | Rule |
|--------:|------|
| **1** (release `261105`) | Uniform **2.0 m** threshold for every leaf |
| **2** (default from the next CoMaps release) | Copernicus-only leaves with `lat_max < 60°N` use **5.0 m**; ≥60°N and all other Mapterhorn classes keep **2.0 m** |

Clients apply overrides the same way regardless of policy version; only the
set of stored points changes. One CoMaps release = one policy version.

### Body schema (`EL02`)

Absolute altitudes are **i32 decimetres** on the logical model. The wire
stores zigzag-deltas of those absolutes (first point’s delta is from
`prev_alt = 0`, so the first override is an absolute).

```
magic           u32     # 0x32304C45 ("EL02" little-endian)
feature_count   u32
for each feature:
  feature_id    u32
  point_count   u16     # full MWM geometry length (alignment)
  n             u16     # number of sparse overrides
  for i in 0..n:
    idx_delta   u16     # point_index if i==0 else point_index − prev
    alt_zz      u32     # zigzag_i32(altitude_dm − prev_alt); prev_alt starts 0
```

Zigzag (i32 ↔ u32): encode `((v << 1) ^ (v >> 31)) as u32`;
decode `((v >> 1) as i32) ^ -((v & 1) as i32)`.

Features sorted by `feature_id`; points ascending `point_index`.

Client: replace CoMaps altitude at `(feature_id, point_index)` with
`altitude_dm / 10.0` metres when an override exists; leave other points
unchanged. Treat `altitude_dm` as a signed integer decimetre (**i32**).

History: an early draft used zigzag-i16 without a magic word and silently
omitted absolutes outside ±3276.7 m. Published Navi sidecars are **EL02
only**; clients must not implement the draft i16 layout. Server inspect /
audit tools may still decode the draft for forensics.

---

## 5. `vehicle_limits` body

Optional. Old clients that do not know the section name **skip** it (reader
rule 2). **Absent section** means limits were not annotated for this leaf
(client warns "not available"). **Present with `count = 0`** means annotated
and no tagged segments matched.

### VL01 layout (current)

```
magic                  4 bytes   "VL01"
tagged_ways            u32
matched_ways           u32
match_rate_milli       u32       # round(match_rate × 1000), 0…1000
non_numeric_no_limit   u32
unknown_value          u32
osm_data_date          u16-len UTF-8
map_release            u16-len UTF-8
count                  u32
records                count × 20 bytes
```

Legacy bodies (count-first, no magic) are still accepted by readers. New bakes
always write VL01. The same coverage object is also stored on each catalog leaf
as `vehicle_limits_coverage` (see [client-fetch-mwm.md](client-fetch-mwm.md)).

Each record (little-endian):

| Offset | Type | Field | Unit / meaning |
|-------:|------|-------|----------------|
| 0 | u32 | `feature_id` | MWM feature id (per leaf) |
| 4 | u16 | `segment_index` | Segment along the feature |
| 6 | u16 | `reserved` | must be `0` |
| 8 | u16 | `max_height_cm` | cm; `0` = no limit |
| 10 | u16 | `max_width_cm` | cm; `0` = no limit |
| 12 | u16 | `max_length_cm` | cm; `0` = no limit |
| 14 | u16 | `max_weight_10kg` | units of 10 kg; `0` = no limit |
| 16 | u16 | `max_axleload_10kg` | units of 10 kg; `0` = no limit |
| 18 | u16 | `max_bogie_10kg` | units of 10 kg; `0` = no limit |

Sort order (ascending): `(feature_id, segment_index)`. Duplicates dropped at
bake. Lookups are exact on `(feature_id, segment_index)`. Direction is not
stored: the limit applies both ways.

**Source tags** (plain values only; `:conditional` and other variants out of
scope): `maxheight`, `maxwidth`, `maxlength`, `maxweight`, `maxaxleload`,
`maxbogieweight`. Words `none` / `default` / `unsigned` / `no-limit` are
**non_numeric_no_limit** (excluded from the match-rate denominator).
`below_default` is **unknown_value** (excluded; listed in the leaf report, not
in records). Lengths are stored in centimetres; masses in units of 10 kg
(tonnes × 100). Other unparseable values are logged and skipped.

**Binding:** Geofabrik extract → filter highway ways with any of the tags →
match to `dump_mwm_roads` features by quantised geometry (quantum `1e-5`),
highway class, and name when both sides have a name. A segment is emitted
only when **both** endpoints lie on the tagged way. Unmatched tagged ways
are listed in the leaf report; never guessed.

**Client rule:** block a segment only when the vehicle value is **strictly
greater** than the stored limit. Missing / zero limit ⇒ allowed. Apply in
the **car** estimator (`CarEstimator::CalcSegmentWeight` / edge feasibility),
not in the wetland Soft/Hard path. Prefer marking the edge impassable over a
huge weight when the CoMaps API supports it.

---

## 6. `named_routes` body

Optional (`Option` semantics above). Old clients that do not know the section
name **skip** it. **Absent section** means named routes were not annotated for
this leaf. **Present with `count = 0`** means annotated and no matching
relations.

CoMaps MWMs ship only a narrow set of route relation types used for routing
(notably ferry / shuttle_train). Named pilgrimage, hiking, bicycle, ski, and
related routes are **not** searchable from the MWM alone; Navi carries them
here as **search stubs** (OSM relation id + kind + representative point +
display name). They do not alter edge costs.

### NR01 layout

```
magic     4 bytes   "NR01"
count     u32
records   variable length
```

Each record (little-endian):

| Field | Type | Meaning |
|-------|------|---------|
| `osm_id` | u64 | OSM relation id |
| `kind` | u8 | see kind table |
| `lat_e7` | i32 | latitude × 1e7 |
| `lon_e7` | i32 | longitude × 1e7 |
| `name_len` | u16 | UTF-8 byte length |
| `name` | bytes | display name (`name`, else `ref`) |

Records sorted by `osm_id`; duplicates dropped at bake.

### Kind codes

| Byte | JSONL / export string | Notes |
|-----:|-----------------------|-------|
| 0 | `other` | Fallback |
| 1 | `hiking` | Official hiking/foot networks + named local hiking/foot |
| 2 | `bicycle` | Official cycle networks (`icn`/`ncn`/`rcn`/`lcn`) + named local bicycle |
| 3 | `pilgrimage` | `route=pilgrimage`, `pilgrimage=yes`, or name/ref pilgrim hints |
| 4 | `horse` | `route=horse` |
| 5 | `running` | `route=running` or `fitness_trail` |
| 6 | `mtb` | Official or named `route=mtb` |
| 7 | `superroute` | `type=superroute` |
| 8 | `ski` | `route=ski` or `piste` (JSONL may say either; wire is `ski`) |
| 9 | `canoe` | `route=canoe` or `kayak` |
| 10 | `inline_skates` | `route=inline_skates` |

### Which OSM routes are included (operators)

Export (`place-source-points export-named-routes`) keeps relation stubs that
have a usable `name` or `ref` and match any of:

- Pilgrimage (`route=pilgrimage`, `pilgrimage=yes`, or pilgrim name/ref hints)
- Hiking / foot with official networks (`iwn`, `nwn`, `rwn`, `lwn`, `nhn`,
  `rhn`, `lhn`) — includes DNT-style national networks via `nwn`
- Bicycle / mtb with official networks (`icn`, `ncn`, `rcn`, `lcn`)
- Horse, running / fitness_trail
- Ski / piste, canoe / kayak, inline_skates
- Named local trails for those modes even without an official `network`
- Superroutes (`type=superroute`)

Point = first member way’s first node (or first child-relation way). Bbox /
poly clip is applied at annotate time.

### Bake path

1. Prepare a filtered OSM routes `.osm.pbf` via
   `scripts/mwm/build-named-routes-pbf.sh` (tags-filter → merge → sort; no OPL
   dedupe) and set `NAVI_MWM_NAMED_ROUTES_PBF` (must be ≥1 MiB /
   `NAVI_MWM_NAMED_ROUTES_MIN_BYTES` or bake aborts).
2. Per leaf, `scripts/mwm/annotate-named-routes.py` clips with `osmium extract
   -p` when a CoMaps border `.poly` exists; if the clip is empty (e.g.
   duplicate-object source PBF), it falls back to exporting the full PBF with
   an in-process `--bbox` (`bbox_fallback` in the leaf report).
3. Binary: `NAVI_PLACE_SOURCE_BIN` or `{repo}/target/release/place-source-points`
   → `export-named-routes --input … --output ….jsonl [--bbox W,S,E,N]`.
4. Bake: `navi-mwm-sidecar bake --named-routes-jsonl …` emits NR01 when the
   flag is passed; without `NAVI_MWM_NAMED_ROUTES_PBF` the section is omitted.
   Finish/publish always re-copies from bake `sidecars/` by content.

Catalog leaf field `named_routes_coverage` (`routes`, `by_kind`) mirrors the
annotate report — see [client-fetch-mwm.md](client-fetch-mwm.md).

### JSONL input shape

One object per line (bake / `load_named_routes_jsonl`):

```json
{"osm_relation_id":12345,"kind":"pilgrimage","name":"St. Olavsleden","ref":"","network":"nwn","lat":61.12,"lon":10.46}
```

| Field | Required | Notes |
|-------|----------|-------|
| `osm_relation_id` | yes | Positive OSM relation id |
| `kind` | no | String from the kind table; unknown → `other` (0) |
| `name` | no | Prefer over `ref` for display |
| `ref` | no | Used when `name` is empty |
| `network` | no | Informational; not stored on the wire |
| `lat` / `lon` | no | Degrees; stored as ×1e7 on the wire |

Rows with neither `name` nor `ref` are skipped. Export CLI:
`place-source-points export-named-routes --input X.osm.pbf --output Y.jsonl
[--bbox west,south,east,north]`.

---

## 7. `attribution` body

Raw UTF-8 JSON (same object as published `attribution.json`). Must parse as
JSON. Show offline (OSM, CoMaps, Mapterhorn, Navi).

---

## 8. Bake JSONL shapes (other sections)

Inputs consumed by `navi-mwm-sidecar bake` (one JSON object per line):

**Wetland** (`--wetland-jsonl`):

```json
{"feature_id":495,"segment_index":45,"direction":0,"class":1,"ambiguous":false}
```

`class`: `1` Soft, `2` Hard. Ambiguous rows above the bake threshold fail.

**Boardwalk** (`--boardwalk-jsonl`):

```json
{"feature_id":10,"segment_index":0,"direction":0,"flag":1,"ambiguous":false}
```

**Elevation** (`--elevation-jsonl`):

```json
{"feature_id":42,"point_count":100,"points":[{"point_index":3,"altitude_dm":1234}]}
```

**Vehicle limits** (`--vehicle-limits-jsonl`):

```json
{"feature_id":7,"segment_index":2,"max_height_cm":400,"max_width_cm":0,"max_length_cm":0,"max_weight_10kg":0,"max_axleload_10kg":0,"max_bogie_10kg":0,"ambiguous":false}
```

Zero limit fields are dropped before encode. Coverage for the VL01 header
comes from the annotate report, not the JSONL rows.

---

## 9. File naming

| Artifact | Name |
|----------|------|
| Published sidecar | `{Leaf}.navi-sidecar.v1.zst` |
| Outer compression | single zstd frame, level **10** |
| Catalog relative URL | `{version}/{Leaf}.navi-sidecar.v1.zst` under `/mwm/` |

---

## 10. Worked example — Norway_Hedmark (release 261105)

Regenerated from the live sidecar
`https://navigate-me.duckdns.org/mwm/261105/Norway_Hedmark.navi-sidecar.v1.zst`.
Checked-in fixture:
[examples/fixtures/Norway_Hedmark.navi-sidecar.v1.zst](examples/fixtures/Norway_Hedmark.navi-sidecar.v1.zst)
with metadata
[examples/fixtures/Norway_Hedmark.sidecar.meta.json](examples/fixtures/Norway_Hedmark.sidecar.meta.json).
CI fails if the docs/example catalog drift from that fixture.

This fixture has four sections only (`wetland_edges`, `boardwalk_flags`,
`elevation_overrides`, `attribution`). Leaves baked with
`NAVI_MWM_VEHICLE_LIMITS_PBF` / `NAVI_MWM_NAMED_ROUTES_PBF` also carry
`vehicle_limits` (VL01) and/or `named_routes` (NR01) before `attribution`.

### Inspect tool output

```json
{
  "format_version": 1,
  "generator_tag": "comaps-release/2026.10.27",
  "generator_sha": "261105",
  "mwm_sha256": "2852a363342183bf4df6b1b78f9790685dd7e6a1bc34eaefbf177fc6432799af",
  "mwm_size": 102194292,
  "wetland_records": 98082,
  "soft_records": 97976,
  "hard_records": 106,
  "boardwalk_records": 220,
  "elevation_features": 10097,
  "elevation_points": 25276,
  "sections": [
    "wetland_edges",
    "boardwalk_flags",
    "elevation_overrides",
    "attribution"
  ],
  "fingerprint_ok": true
}
```

Outer `.zst` size: **267737** bytes
(`sha256=4feb60e8441eee228bb77d5d028e49c3a47e7cae5105e4d0a12e0485ee98363f`).
Logical blob after outer decompress: **329757** bytes. Payload starts at
logical offset **377**.

### Hex — start of logical blob (first 160 bytes)

```
4e565343 01000000          # "NVSC", version 1
1900 636f6d6170732d72656c656173652f323032362e31302e3237
                           # u16=25, "comaps-release/2026.10.27"
0600 323631313035          # u16=6, "261105"
2852a363342183bf4df6b1b78f9790685dd7e6a1bc34eaefbf177fc6432799af
                           # mwm_sha256
745c170600000000           # mwm_size = 102194292
04000000                   # section_count = 4
0d00 7765746c616e645f6564676573
                           # "wetland_edges"
14f90b0000000000           # uncompressed 784660
0217030000000000           # compressed 202498
…sha256…
0000000000000000           # offset 0
```

### First three `wetland_edges` records (after inner decompress)

Body starts `count=98082` (`227f0100`), then:

| # | feature_id | segment | dir | class | hex (8 bytes) |
|--:|----------:|--------:|----:|------:|---------------|
| 0 | 495 | 45 | 0 | Soft(1) | `ef010000 2d00 00 01` |
| 1 | 495 | 45 | 1 | Soft(1) | `ef010000 2d00 01 01` |
| 2 | 495 | 46 | 0 | Soft(1) | `ef010000 2e00 00 01` |

Section sizes (Hedmark):

| Section | Uncompressed | Compressed | Payload offset |
|---------|-------------:|-----------:|---------------:|
| wetland_edges | 784660 | 202498 | 0 |
| boardwalk_flags | 1764 | 491 | 202498 |
| elevation_overrides | 232440 | 112359 | 202989 |
| attribution | 104301 | 14032 | 315348 |

---

## 11. Legacy spike2 flat layout (do not ship)

Older validation files used: `NVSC` + version + mwm_sha256 + mwm_size + count +
flat records (no generator strings, no section directory). The production
client and `navi_wetland_cost.cpp` prefer **sectioned** v1; flat remains a
loader fallback only.
