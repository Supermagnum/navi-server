//! Navi MWM sidecar format (NVSC v1).
//!
//! The `.zst` file is one zstd frame (level 10) around a logical blob:
//! magic, version, generator tag and sha, MWM fingerprint, a section
//! directory, then concatenated per-section zstd bodies. Section names are
//! `wetland_edges`, `boardwalk_flags`, `elevation_overrides`, and `attribution`.
//!
//! Spike2 Ostlandet files use a flat layout (no directory). [`read_spike2_flat_wetland_count`]
//! decodes that layout.

use std::fs::File;
use std::io::{Read, Write};
use std::path::Path;

use anyhow::{bail, Context, Result};
use serde::Deserialize;
use sha2::{Digest, Sha256};

/// Logical-blob magic.
pub const MAGIC: &[u8; 4] = b"NVSC";

/// Sectioned and spike2 flat version written by this crate and by spike2.
pub const FORMAT_VERSION: u32 = 1;

/// Wetland edge section name.
pub const SECTION_WETLAND: &str = "wetland_edges";

/// Boardwalk flag section name.
pub const SECTION_BOARDWALK: &str = "boardwalk_flags";

/// UTF-8 JSON attribution section name.
pub const SECTION_ATTRIBUTION: &str = "attribution";

/// Mapterhorn elevation overrides (integer decimetres, delta-encoded).
pub const SECTION_ELEVATION: &str = "elevation_overrides";

/// Soft class byte (`bog`, `string_bog`, `fen`, untyped wetland).
pub const CLASS_SOFT: u8 = 1;

/// Hard class byte (`swamp`, `reedbed`).
pub const CLASS_HARD: u8 = 2;

/// Wetland subtypes stored as soft.
pub const SOFT_TYPES: &[&str] = &["bog", "string_bog", "fen"];

/// Wetland subtypes stored as hard. `marsh` is omitted.
pub const HARD_TYPES: &[&str] = &["swamp", "reedbed"];

const ZSTD_LEVEL: i32 = 10;
const RECORD_LEN: usize = 8;

/// One wetland edge record.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct WetlandRecord {
    /// MWM feature id.
    pub feature_id: u32,
    /// Segment along the feature.
    pub segment_index: u16,
    /// Travel direction, 0 or 1.
    pub direction: u8,
    /// [`CLASS_SOFT`] or [`CLASS_HARD`].
    pub class: u8,
}

/// One boardwalk carve-out record.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct BoardwalkRecord {
    /// MWM feature id.
    pub feature_id: u32,
    /// Segment along the feature.
    pub segment_index: u16,
    /// Travel direction, 0 or 1.
    pub direction: u8,
    /// Flag byte. Loaders write 1 when the JSONL omits `flag`.
    pub flag: u8,
}

/// One point altitude override for a routable feature.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct ElevationPoint {
    /// Index into the MWM feature geometry.
    pub point_index: u16,
    /// Absolute altitude in integer decimetres (0.1 m).
    pub altitude_dm: i16,
}

/// Elevation overrides for one feature (only points that differ from CoMaps).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ElevationFeature {
    /// MWM feature id.
    pub feature_id: u32,
    /// Full geometry point count (for client alignment).
    pub point_count: u16,
    /// Sparse overrides in ascending `point_index` order.
    pub points: Vec<ElevationPoint>,
}

/// Section directory entry. `offset` is from the start of the payload region.
#[derive(Debug, Clone)]
pub struct SectionInfo {
    /// Section name.
    pub name: String,
    /// Uncompressed section body length.
    pub uncompressed_size: u64,
    /// Zstd frame length.
    pub compressed_size: u64,
    /// SHA-256 of the uncompressed section body.
    pub sha256: [u8; 32],
    /// Offset of the zstd frame within the payload.
    pub offset: u64,
}

/// Decoded sectioned sidecar.
#[derive(Debug, Clone)]
pub struct Sidecar {
    /// Logical format version.
    pub format_version: u32,
    /// Generator name from the header.
    pub generator_tag: String,
    /// Generator revision from the header.
    pub generator_sha: String,
    /// SHA-256 of the sibling MWM.
    pub mwm_sha256: [u8; 32],
    /// Byte length of the sibling MWM.
    pub mwm_size: u64,
    /// Directory in file order.
    pub sections: Vec<SectionInfo>,
    /// Wetland edge records.
    pub wetland: Vec<WetlandRecord>,
    /// Boardwalk flag records.
    pub boardwalk: Vec<BoardwalkRecord>,
    /// Mapterhorn elevation overrides.
    pub elevation: Vec<ElevationFeature>,
    /// Attribution section as UTF-8 JSON text.
    pub attribution_json: String,
}

#[derive(Debug, Deserialize)]
struct ElevationPointJson {
    point_index: u16,
    altitude_dm: i16,
}

#[derive(Debug, Deserialize)]
struct ElevationFeatureJson {
    feature_id: u32,
    point_count: u16,
    points: Vec<ElevationPointJson>,
}

#[derive(Debug, Deserialize)]
struct WetlandJson {
    feature_id: u32,
    segment_index: u16,
    direction: u8,
    class: u8,
    #[serde(default)]
    ambiguous: bool,
}

#[derive(Debug, Deserialize)]
struct BoardwalkJson {
    feature_id: u32,
    segment_index: u16,
    direction: u8,
    #[serde(default = "default_flag")]
    flag: u8,
    #[serde(default)]
    ambiguous: bool,
}

fn default_flag() -> u8 {
    1
}

/// Classify an OSM `wetland` value.
///
/// `None` is untyped wetland and classifies as soft. `marsh` and every other
/// subtype are omitted.
pub fn classify_wetland_value(wetland_val: Option<&str>) -> Option<u8> {
    match wetland_val {
        None => Some(CLASS_SOFT),
        Some(raw) => {
            let value = raw.trim().to_ascii_lowercase();
            if value.is_empty() || SOFT_TYPES.contains(&value.as_str()) {
                Some(CLASS_SOFT)
            } else if HARD_TYPES.contains(&value.as_str()) {
                Some(CLASS_HARD)
            } else {
                None
            }
        }
    }
}

/// SHA-256 and byte length of `path`.
pub fn sha256_file(path: &Path) -> Result<([u8; 32], u64)> {
    let mut file = File::open(path).with_context(|| format!("open {}", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buf = [0u8; 1 << 20];
    let mut size = 0u64;
    loop {
        let n = file.read(&mut buf)?;
        if n == 0 {
            break;
        }
        size = size
            .checked_add(u64::try_from(n).context("read size")?)
            .context("file larger than u64::MAX")?;
        hasher.update(&buf[..n]);
    }
    let dig = hasher.finalize();
    let mut out = [0u8; 32];
    out.copy_from_slice(&dig);
    Ok((out, size))
}

/// Build the outer zstd frame for a sectioned sidecar.
///
/// Records are sorted. Duplicate records are dropped. `attribution_json` must
/// be UTF-8 JSON.
#[allow(clippy::too_many_arguments)]
pub fn build_sidecar_bytes(
    generator_tag: &str,
    generator_sha: &str,
    mwm_sha256: [u8; 32],
    mwm_size: u64,
    mut wetland: Vec<WetlandRecord>,
    mut boardwalk: Vec<BoardwalkRecord>,
    mut elevation: Vec<ElevationFeature>,
    attribution_json: &str,
) -> Result<Vec<u8>> {
    serde_json::from_str::<serde_json::Value>(attribution_json)
        .context("attribution is not JSON")?;
    wetland.sort();
    wetland.dedup();
    boardwalk.sort();
    boardwalk.dedup();
    for feat in &mut elevation {
        feat.points.sort_by_key(|p| p.point_index);
        feat.points.dedup_by_key(|p| p.point_index);
    }
    elevation.sort_by_key(|f| f.feature_id);
    elevation.dedup_by_key(|f| f.feature_id);

    let sections_raw = [
        (SECTION_WETLAND, wetland_body(&wetland)?),
        (SECTION_BOARDWALK, boardwalk_body(&boardwalk)?),
        (SECTION_ELEVATION, elevation_body(&elevation)?),
        (SECTION_ATTRIBUTION, attribution_json.as_bytes().to_vec()),
    ];

    let mut compressed_parts = Vec::with_capacity(sections_raw.len());
    let mut infos = Vec::with_capacity(sections_raw.len());
    let mut offset = 0u64;
    for (name, raw) in &sections_raw {
        let comp = zstd_compress(raw)?;
        let compressed_size = u64::try_from(comp.len()).context("compressed section too large")?;
        infos.push(SectionInfo {
            name: (*name).to_string(),
            uncompressed_size: u64::try_from(raw.len()).context("section too large")?,
            compressed_size,
            sha256: sha256_bytes(raw),
            offset,
        });
        offset = offset
            .checked_add(compressed_size)
            .context("payload offset overflow")?;
        compressed_parts.push(comp);
    }

    let mut logical = Vec::new();
    logical.extend_from_slice(MAGIC);
    logical.extend_from_slice(&FORMAT_VERSION.to_le_bytes());
    write_u16_str(&mut logical, generator_tag)?;
    write_u16_str(&mut logical, generator_sha)?;
    logical.extend_from_slice(&mwm_sha256);
    logical.extend_from_slice(&mwm_size.to_le_bytes());
    let section_count = u32::try_from(infos.len()).context("too many sections")?;
    logical.extend_from_slice(&section_count.to_le_bytes());
    for info in &infos {
        write_u16_str(&mut logical, &info.name)?;
        logical.extend_from_slice(&info.uncompressed_size.to_le_bytes());
        logical.extend_from_slice(&info.compressed_size.to_le_bytes());
        logical.extend_from_slice(&info.sha256);
        logical.extend_from_slice(&info.offset.to_le_bytes());
    }
    for part in compressed_parts {
        logical.extend_from_slice(&part);
    }
    zstd_compress(&logical)
}

/// Write an outer zstd frame to `path`.
pub fn write_sidecar(path: &Path, outer_zstd: &[u8]) -> Result<()> {
    let mut file = File::create(path).with_context(|| format!("create {}", path.display()))?;
    file.write_all(outer_zstd)?;
    Ok(())
}

/// Read a sectioned sidecar `.zst`.
pub fn read_sidecar(path: &Path) -> Result<Sidecar> {
    let comp = std::fs::read(path).with_context(|| format!("read {}", path.display()))?;
    let data = zstd_decompress(&comp)?;
    if data.len() < 8 || &data[0..4] != MAGIC {
        bail!("bad magic");
    }
    let format_version = read_u32(&data, 4)?;
    if format_version != FORMAT_VERSION {
        bail!("unsupported format_version {format_version}");
    }
    let mut off = 8;
    let generator_tag = read_u16_str(&data, &mut off)?;
    let generator_sha = read_u16_str(&data, &mut off)?;
    if off + 32 + 8 + 4 > data.len() {
        bail!("truncated header");
    }
    let mut mwm_sha256 = [0u8; 32];
    mwm_sha256.copy_from_slice(&data[off..off + 32]);
    off += 32;
    let mwm_size = read_u64(&data, off)?;
    off += 8;
    let section_count = read_u32(&data, off)?;
    off += 4;

    let mut sections = Vec::with_capacity(usize::try_from(section_count).unwrap_or(0));
    for _ in 0..section_count {
        let name = read_u16_str(&data, &mut off)?;
        if off + 8 + 8 + 32 + 8 > data.len() {
            bail!("truncated section dir");
        }
        let uncompressed_size = read_u64(&data, off)?;
        off += 8;
        let compressed_size = read_u64(&data, off)?;
        off += 8;
        let mut sha = [0u8; 32];
        sha.copy_from_slice(&data[off..off + 32]);
        off += 32;
        let offset = read_u64(&data, off)?;
        off += 8;
        sections.push(SectionInfo {
            name,
            uncompressed_size,
            compressed_size,
            sha256: sha,
            offset,
        });
    }
    let payload = &data[off..];

    let mut wetland = Vec::new();
    let mut boardwalk = Vec::new();
    let mut elevation = Vec::new();
    let mut attribution_json = String::new();
    for info in &sections {
        let start = usize::try_from(info.offset).context("section offset")?;
        let n = usize::try_from(info.compressed_size).context("section size")?;
        let end = start.checked_add(n).context("section range")?;
        if end > payload.len() {
            bail!("section {} payload out of range", info.name);
        }
        let raw = zstd_decompress(&payload[start..end])?;
        if u64::try_from(raw.len()).unwrap_or(u64::MAX) != info.uncompressed_size {
            bail!("section {} size mismatch", info.name);
        }
        if sha256_bytes(&raw) != info.sha256 {
            bail!("section {} checksum mismatch", info.name);
        }
        match info.name.as_str() {
            SECTION_WETLAND => wetland = parse_wetland_body(&raw)?,
            SECTION_BOARDWALK => boardwalk = parse_boardwalk_body(&raw)?,
            SECTION_ELEVATION => elevation = parse_elevation_body(&raw)?,
            SECTION_ATTRIBUTION => {
                attribution_json = String::from_utf8(raw).context("attribution is not utf-8")?;
                serde_json::from_str::<serde_json::Value>(&attribution_json)
                    .context("attribution is not JSON")?;
            }
            _ => {}
        }
    }

    Ok(Sidecar {
        format_version,
        generator_tag,
        generator_sha,
        mwm_sha256,
        mwm_size,
        sections,
        wetland,
        boardwalk,
        elevation,
        attribution_json,
    })
}

/// Load wetland JSONL. Fails when lines with `"ambiguous": true` exceed `ambiguous_threshold`.
pub fn load_wetland_jsonl(path: &Path, ambiguous_threshold: u32) -> Result<Vec<WetlandRecord>> {
    let text = std::fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    let mut records = Vec::new();
    let mut ambiguous = 0u32;
    for (i, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let row: WetlandJson =
            serde_json::from_str(line).with_context(|| format!("wetland jsonl line {}", i + 1))?;
        if row.ambiguous {
            ambiguous = ambiguous.saturating_add(1);
        }
        if row.class != CLASS_SOFT && row.class != CLASS_HARD {
            bail!("wetland jsonl line {}: unknown class {}", i + 1, row.class);
        }
        records.push(WetlandRecord {
            feature_id: row.feature_id,
            segment_index: row.segment_index,
            direction: row.direction,
            class: row.class,
        });
    }
    if ambiguous > ambiguous_threshold {
        bail!("ambiguous wetland bindings {ambiguous} exceed threshold {ambiguous_threshold}");
    }
    Ok(records)
}

/// Load elevation override JSONL. A missing file yields an empty list.
///
/// Each line: `{"feature_id":u32,"point_count":u16,"points":[{"point_index":u16,"altitude_dm":i16},...]}`.
pub fn load_elevation_jsonl(path: &Path) -> Result<Vec<ElevationFeature>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let text = std::fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    let mut out = Vec::new();
    for (i, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let row: ElevationFeatureJson = serde_json::from_str(line)
            .with_context(|| format!("elevation jsonl line {}", i + 1))?;
        let mut points: Vec<ElevationPoint> = row
            .points
            .into_iter()
            .map(|p| ElevationPoint {
                point_index: p.point_index,
                altitude_dm: p.altitude_dm,
            })
            .collect();
        points.sort_by_key(|p| p.point_index);
        points.dedup_by_key(|p| p.point_index);
        if points.is_empty() {
            continue;
        }
        out.push(ElevationFeature {
            feature_id: row.feature_id,
            point_count: row.point_count,
            points,
        });
    }
    Ok(out)
}

/// Load boardwalk JSONL. A missing file yields an empty list.
///
/// Fails when lines with `"ambiguous": true` exceed `ambiguous_threshold`.
pub fn load_boardwalk_jsonl(path: &Path, ambiguous_threshold: u32) -> Result<Vec<BoardwalkRecord>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let text = std::fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    let mut records = Vec::new();
    let mut ambiguous = 0u32;
    for (i, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let row: BoardwalkJson = serde_json::from_str(line)
            .with_context(|| format!("boardwalk jsonl line {}", i + 1))?;
        if row.ambiguous {
            ambiguous = ambiguous.saturating_add(1);
        }
        records.push(BoardwalkRecord {
            feature_id: row.feature_id,
            segment_index: row.segment_index,
            direction: row.direction,
            flag: row.flag,
        });
    }
    if ambiguous > ambiguous_threshold {
        bail!("ambiguous boardwalk bindings {ambiguous} exceed threshold {ambiguous_threshold}");
    }
    Ok(records)
}

/// Decode a spike2 flat sidecar and return the wetland record count.
///
/// Layout after zstd: `NVSC`, version `u32`, MWM sha256, MWM size `u64`,
/// count `u32`, then count records of 8 bytes.
pub fn read_spike2_flat_wetland_count(path: &Path) -> Result<usize> {
    let comp = std::fs::read(path).with_context(|| format!("read {}", path.display()))?;
    let data = zstd_decompress(&comp)?;
    if data.len() < 52 || &data[0..4] != MAGIC {
        bail!("not spike2 NVSC");
    }
    let version = read_u32(&data, 4)?;
    if version != FORMAT_VERSION {
        bail!("unsupported spike2 version {version}");
    }
    let count_u32 = read_u32(&data, 48)?;
    let count = usize::try_from(count_u32).context("spike2 count")?;
    let need = count
        .checked_mul(RECORD_LEN)
        .and_then(|n| n.checked_add(52))
        .context("spike2 length overflow")?;
    if data.len() != need {
        bail!("spike2 length {} does not match count {count}", data.len());
    }
    let (recs, tail) = data[52..].as_chunks::<RECORD_LEN>();
    if !tail.is_empty() {
        bail!("spike2 trailing bytes");
    }
    for rec in recs {
        let class = rec[7];
        if class != CLASS_SOFT && class != CLASS_HARD {
            bail!("spike2 record has unknown class {class}");
        }
    }
    Ok(count)
}

fn wetland_body(records: &[WetlandRecord]) -> Result<Vec<u8>> {
    let mut body = Vec::with_capacity(4 + records.len() * RECORD_LEN);
    write_count(&mut body, records.len())?;
    for rec in records {
        body.extend_from_slice(&rec.feature_id.to_le_bytes());
        body.extend_from_slice(&rec.segment_index.to_le_bytes());
        body.push(rec.direction);
        body.push(rec.class);
    }
    Ok(body)
}

fn boardwalk_body(records: &[BoardwalkRecord]) -> Result<Vec<u8>> {
    let mut body = Vec::with_capacity(4 + records.len() * RECORD_LEN);
    write_count(&mut body, records.len())?;
    for rec in records {
        body.extend_from_slice(&rec.feature_id.to_le_bytes());
        body.extend_from_slice(&rec.segment_index.to_le_bytes());
        body.push(rec.direction);
        body.push(rec.flag);
    }
    Ok(body)
}

/// Elevation body: feature count, then per feature:
/// `feature_id u32`, `point_count u16`, `n u16`, then n times delta(`point_index`)
/// as u16 and zigzag-delta(`altitude_dm`) as u16.
fn elevation_body(features: &[ElevationFeature]) -> Result<Vec<u8>> {
    let mut body = Vec::new();
    write_count(&mut body, features.len())?;
    for feat in features {
        body.extend_from_slice(&feat.feature_id.to_le_bytes());
        body.extend_from_slice(&feat.point_count.to_le_bytes());
        let n = u16::try_from(feat.points.len()).context("too many elevation points")?;
        body.extend_from_slice(&n.to_le_bytes());
        let mut prev_idx = 0u16;
        let mut prev_alt = 0i16;
        for (i, p) in feat.points.iter().enumerate() {
            let idx_delta = if i == 0 {
                p.point_index
            } else {
                p.point_index
                    .checked_sub(prev_idx)
                    .context("elevation point_index not ascending")?
            };
            body.extend_from_slice(&idx_delta.to_le_bytes());
            let alt_delta = i16::wrapping_sub(p.altitude_dm, prev_alt);
            let zz = zigzag_encode_i16(alt_delta);
            body.extend_from_slice(&zz.to_le_bytes());
            prev_idx = p.point_index;
            prev_alt = p.altitude_dm;
        }
    }
    Ok(body)
}

fn parse_elevation_body(data: &[u8]) -> Result<Vec<ElevationFeature>> {
    if data.len() < 4 {
        bail!("elevation body too short");
    }
    let count = usize::try_from(read_u32(data, 0)?).context("elevation feature count")?;
    let mut off = 4usize;
    let mut out = Vec::with_capacity(count);
    for _ in 0..count {
        if off + 4 + 2 + 2 > data.len() {
            bail!("truncated elevation feature header");
        }
        let feature_id = read_u32(data, off)?;
        off += 4;
        let point_count = read_u16(data, off)?;
        off += 2;
        let n = usize::from(read_u16(data, off)?);
        off += 2;
        let mut points = Vec::with_capacity(n);
        let mut prev_idx = 0u16;
        let mut prev_alt = 0i16;
        for i in 0..n {
            if off + 4 > data.len() {
                bail!("truncated elevation point");
            }
            let idx_delta = read_u16(data, off)?;
            off += 2;
            let zz = read_u16(data, off)?;
            off += 2;
            let point_index = if i == 0 {
                idx_delta
            } else {
                prev_idx
                    .checked_add(idx_delta)
                    .context("elevation point_index overflow")?
            };
            let alt_delta = zigzag_decode_i16(zz);
            let altitude_dm = i16::wrapping_add(prev_alt, alt_delta);
            points.push(ElevationPoint {
                point_index,
                altitude_dm,
            });
            prev_idx = point_index;
            prev_alt = altitude_dm;
        }
        out.push(ElevationFeature {
            feature_id,
            point_count,
            points,
        });
    }
    if off != data.len() {
        bail!("elevation trailing bytes");
    }
    Ok(out)
}

fn zigzag_encode_i16(v: i16) -> u16 {
    ((v << 1) ^ (v >> 15)) as u16
}

fn zigzag_decode_i16(v: u16) -> i16 {
    ((v >> 1) as i16) ^ -((v & 1) as i16)
}

fn write_count(body: &mut Vec<u8>, len: usize) -> Result<()> {
    let count = u32::try_from(len).context("too many records")?;
    body.extend_from_slice(&count.to_le_bytes());
    Ok(())
}

fn parse_wetland_body(data: &[u8]) -> Result<Vec<WetlandRecord>> {
    let (count, rest) = split_records(data, "wetland")?;
    let (recs, tail) = rest.as_chunks::<RECORD_LEN>();
    if !tail.is_empty() {
        bail!("wetland trailing bytes");
    }
    let mut out = Vec::with_capacity(count);
    for rec in recs {
        let class = rec[7];
        if class != CLASS_SOFT && class != CLASS_HARD {
            bail!("wetland record has unknown class {class}");
        }
        out.push(WetlandRecord {
            feature_id: read_u32(rec, 0)?,
            segment_index: read_u16(rec, 4)?,
            direction: rec[6],
            class,
        });
    }
    Ok(out)
}

fn parse_boardwalk_body(data: &[u8]) -> Result<Vec<BoardwalkRecord>> {
    let (count, rest) = split_records(data, "boardwalk")?;
    let (recs, tail) = rest.as_chunks::<RECORD_LEN>();
    if !tail.is_empty() {
        bail!("boardwalk trailing bytes");
    }
    let mut out = Vec::with_capacity(count);
    for rec in recs {
        out.push(BoardwalkRecord {
            feature_id: read_u32(rec, 0)?,
            segment_index: read_u16(rec, 4)?,
            direction: rec[6],
            flag: rec[7],
        });
    }
    Ok(out)
}

fn split_records<'a>(data: &'a [u8], name: &str) -> Result<(usize, &'a [u8])> {
    if data.len() < 4 {
        bail!("{name} body too short");
    }
    let count = usize::try_from(read_u32(data, 0)?).context("record count")?;
    let need = count
        .checked_mul(RECORD_LEN)
        .and_then(|n| n.checked_add(4))
        .context("record length overflow")?;
    if data.len() != need {
        bail!(
            "{name} body length {} does not match count {count}",
            data.len()
        );
    }
    Ok((count, &data[4..]))
}

fn zstd_compress(data: &[u8]) -> Result<Vec<u8>> {
    zstd::bulk::compress(data, ZSTD_LEVEL).context("zstd compress")
}

fn zstd_decompress(data: &[u8]) -> Result<Vec<u8>> {
    zstd::stream::decode_all(std::io::Cursor::new(data)).context("zstd decompress")
}

fn sha256_bytes(data: &[u8]) -> [u8; 32] {
    let dig = Sha256::digest(data);
    let mut out = [0u8; 32];
    out.copy_from_slice(&dig);
    out
}

fn write_u16_str(buf: &mut Vec<u8>, value: &str) -> Result<()> {
    let bytes = value.as_bytes();
    let len = u16::try_from(bytes.len()).context("string longer than u16::MAX")?;
    buf.extend_from_slice(&len.to_le_bytes());
    buf.extend_from_slice(bytes);
    Ok(())
}

fn read_u16_str(data: &[u8], off: &mut usize) -> Result<String> {
    let len = usize::from(read_u16(data, *off)?);
    *off += 2;
    let end = off.checked_add(len).context("string range")?;
    if end > data.len() {
        bail!("truncated string");
    }
    let s = std::str::from_utf8(&data[*off..end])
        .context("string is not utf-8")?
        .to_string();
    *off = end;
    Ok(s)
}

fn read_u16(data: &[u8], off: usize) -> Result<u16> {
    let slice = data.get(off..off + 2).context("truncated u16")?;
    let mut buf = [0u8; 2];
    buf.copy_from_slice(slice);
    Ok(u16::from_le_bytes(buf))
}

fn read_u32(data: &[u8], off: usize) -> Result<u32> {
    let slice = data.get(off..off + 4).context("truncated u32")?;
    let mut buf = [0u8; 4];
    buf.copy_from_slice(slice);
    Ok(u32::from_le_bytes(buf))
}

fn read_u64(data: &[u8], off: usize) -> Result<u64> {
    let slice = data.get(off..off + 8).context("truncated u64")?;
    let mut buf = [0u8; 8];
    buf.copy_from_slice(slice);
    Ok(u64::from_le_bytes(buf))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn classify_policy() {
        assert_eq!(classify_wetland_value(None), Some(CLASS_SOFT));
        assert_eq!(classify_wetland_value(Some("  ")), Some(CLASS_SOFT));
        assert_eq!(classify_wetland_value(Some("bog")), Some(CLASS_SOFT));
        assert_eq!(
            classify_wetland_value(Some(" string_bog ")),
            Some(CLASS_SOFT)
        );
        assert_eq!(classify_wetland_value(Some("FEN")), Some(CLASS_SOFT));
        assert_eq!(classify_wetland_value(Some("swamp")), Some(CLASS_HARD));
        assert_eq!(classify_wetland_value(Some("reedbed")), Some(CLASS_HARD));
        assert_eq!(classify_wetland_value(Some("marsh")), None);
        assert_eq!(classify_wetland_value(Some("Marsh")), None);
        assert_eq!(classify_wetland_value(Some("tidalflat")), None);
    }

    #[test]
    fn roundtrip() {
        let wetland = vec![
            WetlandRecord {
                feature_id: 11,
                segment_index: 0,
                direction: 1,
                class: CLASS_HARD,
            },
            WetlandRecord {
                feature_id: 10,
                segment_index: 1,
                direction: 0,
                class: CLASS_SOFT,
            },
        ];
        let boardwalk = vec![BoardwalkRecord {
            feature_id: 10,
            segment_index: 1,
            direction: 0,
            flag: 1,
        }];
        let elevation = vec![ElevationFeature {
            feature_id: 10,
            point_count: 4,
            points: vec![
                ElevationPoint {
                    point_index: 1,
                    altitude_dm: 1234,
                },
                ElevationPoint {
                    point_index: 3,
                    altitude_dm: 1200,
                },
            ],
        }];
        let bytes = build_sidecar_bytes(
            "v2026.11.07-16",
            "fd11cc6f2256443d4d69112a3c22299d4e0664ce",
            [7u8; 32],
            12345,
            wetland,
            boardwalk,
            elevation,
            r#"{"label":"Mapterhorn"}"#,
        )
        .unwrap();
        let dir = tempdir().unwrap();
        let path = dir.path().join("t.navi-sidecar.v1.zst");
        write_sidecar(&path, &bytes).unwrap();
        let sc = read_sidecar(&path).unwrap();
        assert_eq!(sc.format_version, FORMAT_VERSION);
        assert_eq!(sc.generator_tag, "v2026.11.07-16");
        assert_eq!(sc.mwm_sha256, [7u8; 32]);
        assert_eq!(sc.mwm_size, 12345);
        assert_eq!(
            sc.wetland,
            vec![
                WetlandRecord {
                    feature_id: 10,
                    segment_index: 1,
                    direction: 0,
                    class: CLASS_SOFT,
                },
                WetlandRecord {
                    feature_id: 11,
                    segment_index: 0,
                    direction: 1,
                    class: CLASS_HARD,
                },
            ]
        );
        assert_eq!(sc.boardwalk.len(), 1);
        assert_eq!(sc.elevation.len(), 1);
        assert_eq!(sc.elevation[0].feature_id, 10);
        assert_eq!(sc.elevation[0].points.len(), 2);
        assert_eq!(sc.elevation[0].points[0].altitude_dm, 1234);
        assert_eq!(sc.elevation[0].points[1].altitude_dm, 1200);
        assert!(sc.attribution_json.contains("Mapterhorn"));
        assert_eq!(
            sc.sections
                .iter()
                .map(|s| s.name.as_str())
                .collect::<Vec<_>>(),
            vec![
                SECTION_WETLAND,
                SECTION_BOARDWALK,
                SECTION_ELEVATION,
                SECTION_ATTRIBUTION
            ]
        );
        let mut offset = 0u64;
        for section in &sc.sections {
            assert_eq!(section.offset, offset);
            offset += section.compressed_size;
        }
    }

    #[test]
    fn ambiguous_threshold_fails() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("w.jsonl");
        std::fs::write(
            &path,
            "{\"feature_id\":1,\"segment_index\":0,\"direction\":0,\"class\":1,\"ambiguous\":true}\n",
        )
        .unwrap();
        let err = load_wetland_jsonl(&path, 0).unwrap_err();
        assert!(err.to_string().contains("ambiguous"));
        let ok = load_wetland_jsonl(&path, 1).unwrap();
        assert_eq!(ok.len(), 1);
        assert_eq!(ok[0].class, CLASS_SOFT);
    }

    #[test]
    fn spike2_flat_synthetic_count() {
        let mut body = Vec::new();
        body.extend_from_slice(MAGIC);
        body.extend_from_slice(&FORMAT_VERSION.to_le_bytes());
        body.extend_from_slice(&[0xab; 32]);
        body.extend_from_slice(&99u64.to_le_bytes());
        body.extend_from_slice(&1u32.to_le_bytes());
        body.extend_from_slice(&7u32.to_le_bytes());
        body.extend_from_slice(&3u16.to_le_bytes());
        body.push(1);
        body.push(CLASS_HARD);
        let comp = zstd_compress(&body).unwrap();
        let dir = tempdir().unwrap();
        let path = dir.path().join("spike2.zst");
        std::fs::write(&path, comp).unwrap();
        assert_eq!(read_spike2_flat_wetland_count(&path).unwrap(), 1);
    }
}
