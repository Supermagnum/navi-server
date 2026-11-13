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
`|Mapterhorn − CoMaps| > threshold` (default **2.0 m**) are stored.

```
feature_count   u32
for each feature:
  feature_id    u32
  point_count   u16     # full MWM geometry length (alignment)
  n             u16     # number of sparse overrides
  for i in 0..n:
    idx_delta   u16     # point_index if i==0 else point_index − prev
    alt_zz      u16     # zigzag(altitude_dm − prev_alt); prev_alt starts 0
```

Zigzag (i16 ↔ u16): encode `((v << 1) ^ (v >> 15)) as u16`;
decode `((v >> 1) as i16) ^ -((v & 1) as i16)`.

Features sorted by `feature_id`; points ascending `point_index`.

Client: replace CoMaps altitude at `(feature_id, point_index)` with
`altitude_dm / 10.0` metres when an override exists; leave other points
unchanged.

---

## 5. `attribution` body

Raw UTF-8 JSON (same object as published `attribution.json`). Must parse as
JSON. Show offline (OSM, CoMaps, Mapterhorn, Navi).

---

## 6. Worked example — Norway_Hedmark (release 261105)

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

Outer `.zst` size: **261797** bytes. Logical blob after outer decompress:
**323816** bytes. Payload starts at logical offset **377**.

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
| elevation_overrides | 181884 | 106432 | 202989 |
| attribution | 104266 | 14018 | 309421 |

---

## 7. Legacy spike2 flat layout (do not ship)

Older validation files used: `NVSC` + version + mwm_sha256 + mwm_size + count +
flat records (no generator strings, no section directory). The production
client and `navi_wetland_cost.cpp` prefer **sectioned** v1; flat remains a
loader fallback only.
