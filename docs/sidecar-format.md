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

| Name | Role | Required for Navi wetland routing |
|------|------|-----------------------------------|
| `wetland_edges` | Soft/Hard edge records | **yes** |
| `boardwalk_flags` | Carve-out flags | no (empty OK) |
| `elevation_overrides` | Point altitude overrides | no (empty OK; drape still works) |
| `vehicle_limits` | Per-segment height/width/length/weight caps | no (omit = not available; empty count OK; old clients skip) |
| `attribution` | UTF-8 JSON credits | **yes** for offline display |

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

## 6. `attribution` body

Raw UTF-8 JSON (same object as published `attribution.json`). Must parse as
JSON. Show offline (OSM, CoMaps, Mapterhorn, Navi).

---

## 7. Worked example — Norway_Hedmark (release 261105)

Regenerated from the live sidecar
`https://navigate-me.duckdns.org/mwm/261105/Norway_Hedmark.navi-sidecar.v1.zst`.
Checked-in fixture:
[examples/fixtures/Norway_Hedmark.navi-sidecar.v1.zst](examples/fixtures/Norway_Hedmark.navi-sidecar.v1.zst)
with metadata
[examples/fixtures/Norway_Hedmark.sidecar.meta.json](examples/fixtures/Norway_Hedmark.sidecar.meta.json).
CI fails if the docs/example catalog drift from that fixture.

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

## 8. Legacy spike2 flat layout (do not ship)

Older validation files used: `NVSC` + version + mwm_sha256 + mwm_size + count +
flat records (no generator strings, no section directory). The production
client and `navi_wetland_cost.cpp` prefer **sectioned** v1; flat remains a
loader fallback only.
