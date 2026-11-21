//! Optional `named_routes` NVSC section.
//!
//! Search stubs for named OSM route relations (pilgrimage, hiking, bicycle, …)
//! that CoMaps MWMs do not ship as relation types. Body layout NR01.

use anyhow::{bail, Context, Result};
use serde::Deserialize;

/// Section name (optional; old clients skip unknown sections).
pub const SECTION_NAMED_ROUTES: &str = "named_routes";

/// Magic for the `named_routes` body (`"NR01"`).
pub const NAMED_ROUTES_BODY_MAGIC: &[u8; 4] = b"NR01";

/// Wire kind codes.
pub const KIND_OTHER: u8 = 0;
pub const KIND_HIKING: u8 = 1;
pub const KIND_BICYCLE: u8 = 2;
pub const KIND_PILGRIMAGE: u8 = 3;
pub const KIND_HORSE: u8 = 4;
pub const KIND_RUNNING: u8 = 5;
pub const KIND_MTB: u8 = 6;
pub const KIND_SUPERROUTE: u8 = 7;
pub const KIND_SKI: u8 = 8;
pub const KIND_CANOE: u8 = 9;
pub const KIND_INLINE_SKATES: u8 = 10;

/// One named-route search stub.
#[derive(Debug, Clone, PartialEq)]
pub struct NamedRouteRecord {
    /// OSM relation id.
    pub osm_id: u64,
    /// Kind byte ([`KIND_HIKING`], …).
    pub kind: u8,
    /// Latitude × 1e7.
    pub lat_e7: i32,
    /// Longitude × 1e7.
    pub lon_e7: i32,
    /// Display name (OSM `name`, or `ref` when name is empty).
    pub name: String,
}

#[derive(Debug, Deserialize)]
struct NamedRouteJson {
    osm_relation_id: i64,
    #[serde(default)]
    kind: String,
    #[serde(default)]
    name: String,
    #[serde(default)]
    #[serde(rename = "ref")]
    route_ref: String,
    #[serde(default)]
    lat: f64,
    #[serde(default)]
    lon: f64,
}

/// Map kind string → wire byte.
pub fn kind_from_str(s: &str) -> u8 {
    match s.trim().to_ascii_lowercase().as_str() {
        "hiking" => KIND_HIKING,
        "bicycle" => KIND_BICYCLE,
        "pilgrimage" => KIND_PILGRIMAGE,
        "horse" => KIND_HORSE,
        "running" => KIND_RUNNING,
        "mtb" => KIND_MTB,
        "superroute" => KIND_SUPERROUTE,
        "ski" | "piste" => KIND_SKI,
        "canoe" | "kayak" => KIND_CANOE,
        "inline_skates" => KIND_INLINE_SKATES,
        _ => KIND_OTHER,
    }
}

fn deg_to_e7(v: f64) -> i32 {
    (v * 1e7).round() as i32
}

/// Load named-route JSONL. Missing file → empty.
pub fn load_named_routes_jsonl(path: &std::path::Path) -> Result<Vec<NamedRouteRecord>> {
    if !path.exists() {
        return Ok(Vec::new());
    }
    let text = std::fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    let mut records = Vec::new();
    for (i, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let row: NamedRouteJson = serde_json::from_str(line)
            .with_context(|| format!("named_routes jsonl line {}", i + 1))?;
        if row.osm_relation_id <= 0 {
            bail!(
                "named_routes jsonl line {}: invalid osm_relation_id {}",
                i + 1,
                row.osm_relation_id
            );
        }
        let name = if !row.name.is_empty() {
            row.name
        } else if !row.route_ref.is_empty() {
            row.route_ref
        } else {
            continue;
        };
        records.push(NamedRouteRecord {
            osm_id: row.osm_relation_id as u64,
            kind: kind_from_str(&row.kind),
            lat_e7: deg_to_e7(row.lat),
            lon_e7: deg_to_e7(row.lon),
            name,
        });
    }
    Ok(records)
}

fn write_u16_str(buf: &mut Vec<u8>, value: &str) -> Result<()> {
    let bytes = value.as_bytes();
    let len = u16::try_from(bytes.len()).context("named_routes name too long")?;
    buf.extend_from_slice(&len.to_le_bytes());
    buf.extend_from_slice(bytes);
    Ok(())
}

/// Encode the `named_routes` section body (NR01).
pub fn named_routes_body(records: &[NamedRouteRecord]) -> Result<Vec<u8>> {
    let count = u32::try_from(records.len()).context("too many named_routes records")?;
    let mut body = Vec::new();
    body.extend_from_slice(NAMED_ROUTES_BODY_MAGIC);
    body.extend_from_slice(&count.to_le_bytes());
    for rec in records {
        body.extend_from_slice(&rec.osm_id.to_le_bytes());
        body.push(rec.kind);
        body.extend_from_slice(&rec.lat_e7.to_le_bytes());
        body.extend_from_slice(&rec.lon_e7.to_le_bytes());
        write_u16_str(&mut body, &rec.name)?;
    }
    Ok(body)
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

fn read_i32(data: &[u8], off: usize) -> Result<i32> {
    let slice = data.get(off..off + 4).context("truncated i32")?;
    let mut buf = [0u8; 4];
    buf.copy_from_slice(slice);
    Ok(i32::from_le_bytes(buf))
}

/// Decode the `named_routes` section body (NR01).
pub fn parse_named_routes_body(data: &[u8]) -> Result<Vec<NamedRouteRecord>> {
    if data.len() < 8 {
        bail!("named_routes body too short");
    }
    if &data[0..4] != NAMED_ROUTES_BODY_MAGIC {
        bail!("named_routes bad magic");
    }
    let count = usize::try_from(read_u32(data, 4)?).context("named_routes count")?;
    let mut off = 8usize;
    let mut out = Vec::with_capacity(count);
    for _ in 0..count {
        if off + 8 + 1 + 4 + 4 + 2 > data.len() {
            bail!("named_routes record truncated");
        }
        let osm_id = read_u64(data, off)?;
        off += 8;
        let kind = data[off];
        off += 1;
        let lat_e7 = read_i32(data, off)?;
        off += 4;
        let lon_e7 = read_i32(data, off)?;
        off += 4;
        let name_len = usize::from(read_u16(data, off)?);
        off += 2;
        let end = off
            .checked_add(name_len)
            .context("named_routes name range")?;
        if end > data.len() {
            bail!("named_routes name truncated");
        }
        let name = std::str::from_utf8(&data[off..end])
            .context("named_routes name not utf-8")?
            .to_string();
        off = end;
        out.push(NamedRouteRecord {
            osm_id,
            kind,
            lat_e7,
            lon_e7,
            name,
        });
    }
    if off != data.len() {
        bail!("named_routes trailing bytes");
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nr01_roundtrip() {
        let recs = vec![
            NamedRouteRecord {
                osm_id: 12345,
                kind: KIND_PILGRIMAGE,
                lat_e7: 599_123_456,
                lon_e7: 108_765_432,
                name: "St. Olavsleden".into(),
            },
            NamedRouteRecord {
                osm_id: 99,
                kind: KIND_HIKING,
                lat_e7: 600_000_000,
                lon_e7: 100_000_000,
                name: "R1".into(),
            },
        ];
        let body = named_routes_body(&recs).unwrap();
        assert_eq!(&body[..4], NAMED_ROUTES_BODY_MAGIC);
        let back = parse_named_routes_body(&body).unwrap();
        assert_eq!(back, recs);
    }

    #[test]
    fn jsonl_name_falls_back_to_ref() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("nr.jsonl");
        std::fs::write(
            &p,
            r#"{"osm_relation_id":7,"kind":"bicycle","name":"","ref":"NCN1","network":"ncn","lat":51.5,"lon":-0.1}
"#,
        )
        .unwrap();
        let recs = load_named_routes_jsonl(&p).unwrap();
        assert_eq!(recs.len(), 1);
        assert_eq!(recs[0].osm_id, 7);
        assert_eq!(recs[0].kind, KIND_BICYCLE);
        assert_eq!(recs[0].name, "NCN1");
        assert_eq!(recs[0].lat_e7, 515_000_000);
        assert_eq!(recs[0].lon_e7, -1_000_000);
    }

    #[test]
    fn optional_section_omitted_when_none() {
        use crate::{build_sidecar_bytes, read_sidecar, write_sidecar};
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("no-nr.zst");
        let bytes = build_sidecar_bytes(
            "tag",
            "sha",
            [3u8; 32],
            10,
            Vec::new(),
            Vec::new(),
            Vec::new(),
            None,
            None,
            "{}",
        )
        .unwrap();
        write_sidecar(&path, &bytes).unwrap();
        let sc = read_sidecar(&path).unwrap();
        assert!(!sc.sections.iter().any(|s| s.name == SECTION_NAMED_ROUTES));
        assert!(sc.named_routes.is_empty());

        let path2 = dir.path().join("empty-nr.zst");
        let bytes2 = build_sidecar_bytes(
            "tag",
            "sha",
            [3u8; 32],
            10,
            Vec::new(),
            Vec::new(),
            Vec::new(),
            None,
            Some(Vec::new()),
            "{}",
        )
        .unwrap();
        write_sidecar(&path2, &bytes2).unwrap();
        let sc2 = read_sidecar(&path2).unwrap();
        assert!(sc2
            .sections
            .iter()
            .any(|s| s.name == SECTION_NAMED_ROUTES));
        assert!(sc2.named_routes.is_empty());
    }
}
