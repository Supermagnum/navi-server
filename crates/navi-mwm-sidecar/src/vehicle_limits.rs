//! Optional `vehicle_limits` NVSC section.
//!
//! One record per `(feature_id, segment_index)`. Zero in a limit field means
//! unknown / not tagged and must never block routing. Direction is not stored:
//! the limit applies to both travel directions.

use anyhow::{bail, Context, Result};
use serde::Deserialize;

/// Section name (optional; old clients skip unknown sections).
pub const SECTION_VEHICLE_LIMITS: &str = "vehicle_limits";

/// Fixed record length on the wire.
pub const VEHICLE_LIMIT_RECORD_LEN: usize = 20;

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

/// Encode the `vehicle_limits` section body.
pub fn vehicle_limits_body(records: &[VehicleLimitRecord]) -> Result<Vec<u8>> {
    let mut body = Vec::with_capacity(4 + records.len() * VEHICLE_LIMIT_RECORD_LEN);
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
    Ok(body)
}

/// Decode the `vehicle_limits` section body.
pub fn parse_vehicle_limits_body(data: &[u8]) -> Result<Vec<VehicleLimitRecord>> {
    if data.len() < 4 {
        bail!("vehicle_limits body too short");
    }
    let count = u32::from_le_bytes([data[0], data[1], data[2], data[3]]) as usize;
    let need = 4usize
        .checked_add(
            count
                .checked_mul(VEHICLE_LIMIT_RECORD_LEN)
                .context("vehicle_limits length overflow")?,
        )
        .context("vehicle_limits length overflow")?;
    if data.len() != need {
        bail!(
            "vehicle_limits body length {} does not match count {count}",
            data.len()
        );
    }
    let mut out = Vec::with_capacity(count);
    let mut off = 4;
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
