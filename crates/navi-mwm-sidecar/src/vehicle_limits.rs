//! Optional `vehicle_limits` NVSC section.
//!
//! One record per `(feature_id, segment_index)`. Zero in a limit field means
//! unknown / not tagged and must never block routing. Direction is not stored:
//! the limit applies to both travel directions.
//!
//! Body layout (VL01): coverage header then `count` + 20-byte records. Legacy
//! bodies (count-first, no magic) are still accepted by the reader.

use anyhow::{bail, Context, Result};
use serde::Deserialize;

/// Section name (optional; old clients skip unknown sections).
pub const SECTION_VEHICLE_LIMITS: &str = "vehicle_limits";

/// Fixed record length on the wire.
pub const VEHICLE_LIMIT_RECORD_LEN: usize = 20;

/// Magic for the coverage-prefixed `vehicle_limits` body (`"VL01"` LE).
pub const VEHICLE_LIMITS_BODY_MAGIC: &[u8; 4] = b"VL01";

/// Per-leaf match coverage stored ahead of the record table.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct VehicleLimitsCoverage {
    pub tagged_ways: u32,
    pub matched_ways: u32,
    /// `round(match_rate * 1000)` (0…1000).
    pub match_rate_milli: u32,
    pub non_numeric_no_limit: u32,
    pub unknown_value: u32,
    pub osm_data_date: String,
    pub map_release: String,
}

impl VehicleLimitsCoverage {
    pub fn from_match_rate(rate: f64) -> u32 {
        if !rate.is_finite() || rate <= 0.0 {
            return 0;
        }
        if rate >= 1.0 {
            return 1000;
        }
        (rate * 1000.0).round() as u32
    }

    pub fn match_rate(&self) -> f64 {
        f64::from(self.match_rate_milli) / 1000.0
    }
}

/// One vehicle-limit record for a road segment.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct VehicleLimitRecord {
    /// MWM feature id (per leaf).
    pub feature_id: u32,
    /// Segment along the feature.
    pub segment_index: u16,
    /// Max height in centimetres (0 = none).
    pub max_height_cm: u16,
    /// Max width in centimetres (0 = none).
    pub max_width_cm: u16,
    /// Max length in centimetres (0 = none).
    pub max_length_cm: u16,
    /// Max total weight in units of 10 kg (0 = none).
    pub max_weight_10kg: u16,
    /// Max axle load in units of 10 kg (0 = none).
    pub max_axleload_10kg: u16,
    /// Max bogie weight in units of 10 kg (0 = none).
    pub max_bogie_10kg: u16,
}

#[derive(Debug, Deserialize)]
struct VehicleLimitJson {
    feature_id: u32,
    segment_index: u16,
    #[serde(default)]
    max_height_cm: u16,
    #[serde(default)]
    max_width_cm: u16,
    #[serde(default)]
    max_length_cm: u16,
    #[serde(default)]
    max_weight_10kg: u16,
    #[serde(default)]
    max_axleload_10kg: u16,
    #[serde(default)]
    max_bogie_10kg: u16,
    #[serde(default)]
    ambiguous: bool,
}

impl VehicleLimitRecord {
    /// True when every limit field is zero (nothing to store).
    pub fn is_empty(&self) -> bool {
        self.max_height_cm == 0
            && self.max_width_cm == 0
            && self.max_length_cm == 0
            && self.max_weight_10kg == 0
            && self.max_axleload_10kg == 0
            && self.max_bogie_10kg == 0
    }
}

/// Load vehicle-limit JSONL. Missing file → empty. Ambiguous rows above the
/// threshold fail the bake.
pub fn load_vehicle_limits_jsonl(
    path: &std::path::Path,
    ambiguous_threshold: u32,
) -> Result<Vec<VehicleLimitRecord>> {
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
        let row: VehicleLimitJson = serde_json::from_str(line)
            .with_context(|| format!("vehicle_limits jsonl line {}", i + 1))?;
        if row.ambiguous {
            ambiguous = ambiguous.saturating_add(1);
        }
        let rec = VehicleLimitRecord {
            feature_id: row.feature_id,
            segment_index: row.segment_index,
            max_height_cm: row.max_height_cm,
            max_width_cm: row.max_width_cm,
            max_length_cm: row.max_length_cm,
            max_weight_10kg: row.max_weight_10kg,
            max_axleload_10kg: row.max_axleload_10kg,
            max_bogie_10kg: row.max_bogie_10kg,
        };
        if rec.is_empty() {
            continue;
        }
        records.push(rec);
    }
    if ambiguous > ambiguous_threshold {
        bail!(
            "ambiguous vehicle_limits bindings {ambiguous} exceed threshold {ambiguous_threshold}"
        );
    }
    Ok(records)
}

fn write_u16_str(buf: &mut Vec<u8>, s: &str) -> Result<()> {
    let bytes = s.as_bytes();
    let len = u16::try_from(bytes.len()).context("vehicle_limits string too long")?;
    buf.extend_from_slice(&len.to_le_bytes());
    buf.extend_from_slice(bytes);
    Ok(())
}

fn read_u16_str(data: &[u8], off: &mut usize) -> Result<String> {
    if *off + 2 > data.len() {
        bail!("vehicle_limits coverage string length truncated");
    }
    let len = u16::from_le_bytes(data[*off..*off + 2].try_into().unwrap()) as usize;
    *off += 2;
    if *off + len > data.len() {
        bail!("vehicle_limits coverage string truncated");
    }
    let s = std::str::from_utf8(&data[*off..*off + len])
        .context("vehicle_limits coverage string not utf-8")?
        .to_string();
    *off += len;
    Ok(s)
}

fn append_records(body: &mut Vec<u8>, records: &[VehicleLimitRecord]) -> Result<()> {
    let count = u32::try_from(records.len()).context("too many vehicle_limits records")?;
    body.extend_from_slice(&count.to_le_bytes());
    for rec in records {
        body.extend_from_slice(&rec.feature_id.to_le_bytes());
        body.extend_from_slice(&rec.segment_index.to_le_bytes());
        body.extend_from_slice(&0u16.to_le_bytes()); // reserved
        body.extend_from_slice(&rec.max_height_cm.to_le_bytes());
        body.extend_from_slice(&rec.max_width_cm.to_le_bytes());
        body.extend_from_slice(&rec.max_length_cm.to_le_bytes());
        body.extend_from_slice(&rec.max_weight_10kg.to_le_bytes());
        body.extend_from_slice(&rec.max_axleload_10kg.to_le_bytes());
        body.extend_from_slice(&rec.max_bogie_10kg.to_le_bytes());
    }
    Ok(())
}

fn parse_records(data: &[u8], mut off: usize) -> Result<Vec<VehicleLimitRecord>> {
    if off + 4 > data.len() {
        bail!("vehicle_limits count truncated");
    }
    let count = u32::from_le_bytes(data[off..off + 4].try_into().unwrap()) as usize;
    off += 4;
    let need = off
        .checked_add(
            count
                .checked_mul(VEHICLE_LIMIT_RECORD_LEN)
                .context("vehicle_limits length overflow")?,
        )
        .context("vehicle_limits length overflow")?;
    if data.len() != need {
        bail!(
            "vehicle_limits body length {} does not match count {count} (off {off})",
            data.len()
        );
    }
    let mut out = Vec::with_capacity(count);
    for _ in 0..count {
        let feature_id = u32::from_le_bytes(data[off..off + 4].try_into().unwrap());
        off += 4;
        let segment_index = u16::from_le_bytes(data[off..off + 2].try_into().unwrap());
        off += 2;
        off += 2; // reserved
        let max_height_cm = u16::from_le_bytes(data[off..off + 2].try_into().unwrap());
        off += 2;
        let max_width_cm = u16::from_le_bytes(data[off..off + 2].try_into().unwrap());
        off += 2;
        let max_length_cm = u16::from_le_bytes(data[off..off + 2].try_into().unwrap());
        off += 2;
        let max_weight_10kg = u16::from_le_bytes(data[off..off + 2].try_into().unwrap());
        off += 2;
        let max_axleload_10kg = u16::from_le_bytes(data[off..off + 2].try_into().unwrap());
        off += 2;
        let max_bogie_10kg = u16::from_le_bytes(data[off..off + 2].try_into().unwrap());
        off += 2;
        out.push(VehicleLimitRecord {
            feature_id,
            segment_index,
            max_height_cm,
            max_width_cm,
            max_length_cm,
            max_weight_10kg,
            max_axleload_10kg,
            max_bogie_10kg,
        });
    }
    Ok(out)
}

/// Encode the `vehicle_limits` section body with a VL01 coverage header.
pub fn vehicle_limits_body_with_coverage(
    records: &[VehicleLimitRecord],
    coverage: &VehicleLimitsCoverage,
) -> Result<Vec<u8>> {
    let mut body = Vec::with_capacity(64 + records.len() * VEHICLE_LIMIT_RECORD_LEN);
    body.extend_from_slice(VEHICLE_LIMITS_BODY_MAGIC);
    body.extend_from_slice(&coverage.tagged_ways.to_le_bytes());
    body.extend_from_slice(&coverage.matched_ways.to_le_bytes());
    body.extend_from_slice(&coverage.match_rate_milli.to_le_bytes());
    body.extend_from_slice(&coverage.non_numeric_no_limit.to_le_bytes());
    body.extend_from_slice(&coverage.unknown_value.to_le_bytes());
    write_u16_str(&mut body, &coverage.osm_data_date)?;
    write_u16_str(&mut body, &coverage.map_release)?;
    append_records(&mut body, records)?;
    Ok(body)
}

/// Encode the `vehicle_limits` section body (VL01 with empty coverage).
pub fn vehicle_limits_body(records: &[VehicleLimitRecord]) -> Result<Vec<u8>> {
    vehicle_limits_body_with_coverage(records, &VehicleLimitsCoverage::default())
}

/// Decode the `vehicle_limits` section body (VL01 or legacy count-first).
pub fn parse_vehicle_limits_section(
    data: &[u8],
) -> Result<(VehicleLimitsCoverage, Vec<VehicleLimitRecord>)> {
    if data.len() < 4 {
        bail!("vehicle_limits body too short");
    }
    if data[..4] == *VEHICLE_LIMITS_BODY_MAGIC {
        let mut off = 4;
        if off + 20 > data.len() {
            bail!("vehicle_limits VL01 coverage truncated");
        }
        let tagged_ways = u32::from_le_bytes(data[off..off + 4].try_into().unwrap());
        off += 4;
        let matched_ways = u32::from_le_bytes(data[off..off + 4].try_into().unwrap());
        off += 4;
        let match_rate_milli = u32::from_le_bytes(data[off..off + 4].try_into().unwrap());
        off += 4;
        let non_numeric_no_limit = u32::from_le_bytes(data[off..off + 4].try_into().unwrap());
        off += 4;
        let unknown_value = u32::from_le_bytes(data[off..off + 4].try_into().unwrap());
        off += 4;
        let osm_data_date = read_u16_str(data, &mut off)?;
        let map_release = read_u16_str(data, &mut off)?;
        let records = parse_records(data, off)?;
        return Ok((
            VehicleLimitsCoverage {
                tagged_ways,
                matched_ways,
                match_rate_milli,
                non_numeric_no_limit,
                unknown_value,
                osm_data_date,
                map_release,
            },
            records,
        ));
    }
    // Legacy: count + records only.
    let records = parse_records(data, 0)?;
    Ok((VehicleLimitsCoverage::default(), records))
}

/// Decode records only (legacy callers).
pub fn parse_vehicle_limits_body(data: &[u8]) -> Result<Vec<VehicleLimitRecord>> {
    Ok(parse_vehicle_limits_section(data)?.1)
}

/// Binary search for `(feature_id, segment_index)` in a sorted record list.
pub fn lookup_vehicle_limit(
    records: &[VehicleLimitRecord],
    feature_id: u32,
    segment_index: u16,
) -> Option<VehicleLimitRecord> {
    records
        .binary_search_by_key(&(feature_id, segment_index), |r| {
            (r.feature_id, r.segment_index)
        })
        .ok()
        .map(|i| records[i])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_body() {
        let recs = vec![
            VehicleLimitRecord {
                feature_id: 10,
                segment_index: 0,
                max_height_cm: 270,
                max_width_cm: 0,
                max_length_cm: 0,
                max_weight_10kg: 0,
                max_axleload_10kg: 0,
                max_bogie_10kg: 0,
            },
            VehicleLimitRecord {
                feature_id: 42,
                segment_index: 3,
                max_height_cm: 370,
                max_width_cm: 350,
                max_length_cm: 1240,
                max_weight_10kg: 750, // 7.5 t
                max_axleload_10kg: 600,
                max_bogie_10kg: 750,
            },
        ];
        let body = vehicle_limits_body(&recs).unwrap();
        let back = parse_vehicle_limits_body(&body).unwrap();
        assert_eq!(back, recs);
        assert_eq!(
            lookup_vehicle_limit(&back, 42, 3).unwrap().max_weight_10kg,
            750
        );
        assert!(lookup_vehicle_limit(&back, 42, 4).is_none());
    }

    #[test]
    fn vl01_coverage_round_trip() {
        let recs = vec![VehicleLimitRecord {
            feature_id: 1,
            segment_index: 0,
            max_height_cm: 400,
            max_width_cm: 0,
            max_length_cm: 0,
            max_weight_10kg: 0,
            max_axleload_10kg: 0,
            max_bogie_10kg: 0,
        }];
        let cov = VehicleLimitsCoverage {
            tagged_ways: 10,
            matched_ways: 9,
            match_rate_milli: 900,
            non_numeric_no_limit: 1,
            unknown_value: 0,
            osm_data_date: "2026-11-05".into(),
            map_release: "261105".into(),
        };
        let body = vehicle_limits_body_with_coverage(&recs, &cov).unwrap();
        assert_eq!(&body[..4], VEHICLE_LIMITS_BODY_MAGIC);
        let (back_cov, back_recs) = parse_vehicle_limits_section(&body).unwrap();
        assert_eq!(back_cov, cov);
        assert_eq!(back_recs, recs);
        assert!((back_cov.match_rate() - 0.9).abs() < 1e-9);
    }

    #[test]
    fn legacy_count_first_still_parses() {
        let mut legacy = Vec::new();
        legacy.extend_from_slice(&1u32.to_le_bytes());
        legacy.extend_from_slice(&7u32.to_le_bytes());
        legacy.extend_from_slice(&0u16.to_le_bytes());
        legacy.extend_from_slice(&0u16.to_le_bytes());
        legacy.extend_from_slice(&250u16.to_le_bytes());
        for _ in 0..5 {
            legacy.extend_from_slice(&0u16.to_le_bytes());
        }
        let (cov, recs) = parse_vehicle_limits_section(&legacy).unwrap();
        assert_eq!(cov, VehicleLimitsCoverage::default());
        assert_eq!(recs.len(), 1);
        assert_eq!(recs[0].feature_id, 7);
        assert_eq!(recs[0].max_height_cm, 250);
    }

    #[test]
    fn empty_records_dropped_from_jsonl() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("vl.jsonl");
        std::fs::write(
            &p,
            r#"{"feature_id":1,"segment_index":0}
{"feature_id":2,"segment_index":1,"max_height_cm":250}
"#,
        )
        .unwrap();
        let recs = load_vehicle_limits_jsonl(&p, 0).unwrap();
        assert_eq!(recs.len(), 1);
        assert_eq!(recs[0].feature_id, 2);
        assert_eq!(recs[0].max_height_cm, 250);
    }

    #[test]
    fn optional_section_omitted_when_none() {
        use crate::{build_sidecar_bytes, read_sidecar, write_sidecar};
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("no-vl.zst");
        let bytes = build_sidecar_bytes(
            "tag",
            "sha",
            [2u8; 32],
            10,
            Vec::new(),
            Vec::new(),
            Vec::new(),
            None,
            "{}",
        )
        .unwrap();
        write_sidecar(&path, &bytes).unwrap();
        let sc = read_sidecar(&path).unwrap();
        assert!(!sc.sections.iter().any(|s| s.name == SECTION_VEHICLE_LIMITS));
        assert!(sc.vehicle_limits.is_empty());

        let path2 = dir.path().join("empty-vl.zst");
        let bytes2 = build_sidecar_bytes(
            "tag",
            "sha",
            [2u8; 32],
            10,
            Vec::new(),
            Vec::new(),
            Vec::new(),
            Some(Vec::new()),
            "{}",
        )
        .unwrap();
        write_sidecar(&path2, &bytes2).unwrap();
        let sc2 = read_sidecar(&path2).unwrap();
        assert!(sc2
            .sections
            .iter()
            .any(|s| s.name == SECTION_VEHICLE_LIMITS));
        assert!(sc2.vehicle_limits.is_empty());
    }
}
