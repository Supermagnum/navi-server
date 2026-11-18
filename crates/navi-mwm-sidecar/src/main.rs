use std::path::PathBuf;

use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use navi_mwm_sidecar::{
    build_sidecar_bytes_with_vl_coverage, load_boardwalk_jsonl, load_elevation_jsonl,
    load_vehicle_limits_jsonl, load_wetland_jsonl, read_sidecar, read_spike2_flat_wetland_records,
    sha256_file, wetland_class_counts, write_sidecar, VehicleLimitsCoverage, CLASS_HARD,
    CLASS_SOFT,
};
use serde_json::json;

#[derive(Parser)]
#[command(name = "navi-mwm-sidecar", about = "Build/inspect Navi MWM sidecars")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    Bake {
        #[arg(long)]
        mwm: PathBuf,
        #[arg(long)]
        wetland_jsonl: PathBuf,
        #[arg(long)]
        boardwalk_jsonl: Option<PathBuf>,
        #[arg(long)]
        elevation_jsonl: Option<PathBuf>,
        #[arg(long)]
        vehicle_limits_jsonl: Option<PathBuf>,
        /// Optional annotate report JSON (or coverage-only JSON) for the VL01 header.
        #[arg(long)]
        vehicle_limits_report: Option<PathBuf>,
        #[arg(long)]
        attribution: PathBuf,
        #[arg(long)]
        generator_tag: String,
        #[arg(long)]
        generator_sha: String,
        #[arg(long)]
        out: PathBuf,
        #[arg(long, default_value_t = 0)]
        ambiguous_threshold: u32,
    },
    Inspect {
        #[arg(long)]
        sidecar: PathBuf,
        #[arg(long)]
        mwm: Option<PathBuf>,
    },
    CompareCounts {
        #[arg(long)]
        sidecar: PathBuf,
        #[arg(long)]
        expect_wetland: usize,
    },
    /// Compare Soft/Hard wetland counts against a spike2-style JSONL or sidecar.
    CompareSpike2 {
        #[arg(long)]
        sidecar: PathBuf,
        /// Spike2 flat sidecar OR sectioned sidecar OR wetland JSONL.
        #[arg(long)]
        spike2: PathBuf,
    },
}

fn hex_encode(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn coverage_from_report_json(path: &PathBuf) -> Result<VehicleLimitsCoverage> {
    let text = std::fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    let v: serde_json::Value =
        serde_json::from_str(&text).with_context(|| format!("parse {}", path.display()))?;
    let tagged = v.get("tagged_ways").and_then(|x| x.as_u64()).unwrap_or(0) as u32;
    let matched = v.get("matched_ways").and_then(|x| x.as_u64()).unwrap_or(0) as u32;
    let nn = v
        .get("non_numeric_no_limit")
        .and_then(|x| x.as_u64())
        .unwrap_or(0) as u32;
    let unk = v.get("unknown_value").and_then(|x| x.as_u64()).unwrap_or(0) as u32;
    let rate = v
        .get("match_rate")
        .and_then(|x| x.as_f64())
        .unwrap_or_else(|| {
            let numeric = v
                .get("numeric_ways")
                .and_then(|x| x.as_u64())
                .unwrap_or_else(|| {
                    let skipped = v
                        .get("skipped_unparseable")
                        .and_then(|x| x.as_u64())
                        .unwrap_or(0);
                    tagged.saturating_sub(skipped as u32) as u64
                });
            if numeric == 0 {
                0.0
            } else {
                f64::from(matched) / numeric as f64
            }
        });
    Ok(VehicleLimitsCoverage {
        tagged_ways: tagged,
        matched_ways: matched,
        match_rate_milli: VehicleLimitsCoverage::from_match_rate(rate),
        non_numeric_no_limit: nn,
        unknown_value: unk,
        osm_data_date: v
            .get("osm_data_date")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string(),
        map_release: v
            .get("map_release")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string(),
    })
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    match cli.cmd {
        Cmd::Bake {
            mwm,
            wetland_jsonl,
            boardwalk_jsonl,
            elevation_jsonl,
            vehicle_limits_jsonl,
            vehicle_limits_report,
            attribution,
            generator_tag,
            generator_sha,
            out,
            ambiguous_threshold,
        } => {
            let (mwm_sha, mwm_size) = sha256_file(&mwm)?;
            let wetland = load_wetland_jsonl(&wetland_jsonl, ambiguous_threshold)?;
            let boardwalk = match boardwalk_jsonl {
                Some(p) => load_boardwalk_jsonl(&p, ambiguous_threshold)?,
                None => Vec::new(),
            };
            let elevation = match elevation_jsonl {
                Some(p) => load_elevation_jsonl(&p)?,
                None => Vec::new(),
            };
            // Flag present ⇒ emit section (even if zero records). Flag absent ⇒
            // omit section so clients can warn "limits not available".
            let vehicle_limits = match vehicle_limits_jsonl {
                Some(p) => Some(load_vehicle_limits_jsonl(&p, ambiguous_threshold)?),
                None => None,
            };
            let vl_coverage = match vehicle_limits_report {
                Some(p) => coverage_from_report_json(&p)?,
                None => VehicleLimitsCoverage::default(),
            };
            let attr = std::fs::read_to_string(&attribution)
                .with_context(|| format!("read {}", attribution.display()))?;
            let bytes = build_sidecar_bytes_with_vl_coverage(
                &generator_tag,
                &generator_sha,
                mwm_sha,
                mwm_size,
                wetland,
                boardwalk,
                elevation,
                vehicle_limits,
                &vl_coverage,
                &attr,
            )?;
            write_sidecar(&out, &bytes)?;
            let sc = read_sidecar(&out)?;
            let elev_points: usize = sc.elevation.iter().map(|f| f.points.len()).sum();
            println!(
                "{}",
                json!({
                    "out": out.display().to_string(),
                    "mwm_sha256": hex_encode(&sc.mwm_sha256),
                    "mwm_bytes": sc.mwm_size,
                    "wetland_records": sc.wetland.len(),
                    "boardwalk_records": sc.boardwalk.len(),
                    "elevation_features": sc.elevation.len(),
                    "elevation_points": elev_points,
                    "vehicle_limits_records": sc.vehicle_limits.len(),
                    "vehicle_limits_coverage": {
                        "tagged_ways": sc.vehicle_limits_coverage.tagged_ways,
                        "matched_ways": sc.vehicle_limits_coverage.matched_ways,
                        "match_rate": sc.vehicle_limits_coverage.match_rate(),
                        "non_numeric_no_limit": sc.vehicle_limits_coverage.non_numeric_no_limit,
                        "unknown_value": sc.vehicle_limits_coverage.unknown_value,
                        "osm_data_date": sc.vehicle_limits_coverage.osm_data_date,
                        "map_release": sc.vehicle_limits_coverage.map_release,
                    },
                })
            );
        }
        Cmd::Inspect { sidecar, mwm } => {
            let sc = read_sidecar(&sidecar)?;
            let mut fingerprint_ok = true;
            let mut fingerprint_ok_sha = hex_encode(&sc.mwm_sha256);
            if let Some(m) = mwm {
                let (sha, size) = sha256_file(&m)?;
                fingerprint_ok = sha == sc.mwm_sha256 && size == sc.mwm_size;
                fingerprint_ok_sha = hex_encode(&sha);
            }
            let elev_points: usize = sc.elevation.iter().map(|f| f.points.len()).sum();
            let (soft, hard) = wetland_class_counts(&sc.wetland);
            let section_details: Vec<_> = sc
                .sections
                .iter()
                .map(|s| {
                    json!({
                        "name": s.name,
                        "uncompressed_size": s.uncompressed_size,
                        "compressed_size": s.compressed_size,
                        "sha256": hex_encode(&s.sha256),
                    })
                })
                .collect();
            println!(
                "{}",
                json!({
                    "format_version": sc.format_version,
                    "generator_tag": sc.generator_tag,
                    "generator_sha": sc.generator_sha,
                    "mwm_sha256": hex_encode(&sc.mwm_sha256),
                    "mwm_size": sc.mwm_size,
                    "wetland_records": sc.wetland.len(),
                    "soft_records": soft,
                    "hard_records": hard,
                    "boardwalk_records": sc.boardwalk.len(),
                    "elevation_features": sc.elevation.len(),
                    "elevation_points": elev_points,
                    "vehicle_limits_records": sc.vehicle_limits.len(),
                    "sections": sc.sections.iter().map(|s| &s.name).collect::<Vec<_>>(),
                    "section_details": section_details,
                    "fingerprint_ok": fingerprint_ok,
                    "fingerprint_ok_sha": fingerprint_ok_sha,
                })
            );
        }
        Cmd::CompareCounts {
            sidecar,
            expect_wetland,
        } => {
            let sc = read_sidecar(&sidecar)?;
            if sc.wetland.len() != expect_wetland {
                bail!(
                    "wetland count {} != expected {}",
                    sc.wetland.len(),
                    expect_wetland
                );
            }
            let (soft, hard) = wetland_class_counts(&sc.wetland);
            println!(
                "{}",
                json!({
                    "ok": true,
                    "wetland_records": sc.wetland.len(),
                    "soft_records": soft,
                    "hard_records": hard,
                })
            );
        }
        Cmd::CompareSpike2 { sidecar, spike2 } => {
            let sc = read_sidecar(&sidecar)?;
            let (soft, hard) = wetland_class_counts(&sc.wetland);
            let spike_records = if spike2
                .extension()
                .is_some_and(|e| e == "jsonl" || e == "json")
            {
                load_wetland_jsonl(&spike2, u32::MAX)?
            } else {
                match read_sidecar(&spike2) {
                    Ok(other) => other.wetland,
                    Err(_) => read_spike2_flat_wetland_records(&spike2)?,
                }
            };
            let (s_soft, s_hard) = wetland_class_counts(&spike_records);
            println!(
                "{}",
                json!({
                    "sidecar": sidecar.display().to_string(),
                    "spike2": spike2.display().to_string(),
                    "release": {
                        "wetland_records": sc.wetland.len(),
                        "soft_records": soft,
                        "hard_records": hard,
                        "soft_share": if sc.wetland.is_empty() {
                            0.0
                        } else {
                            soft as f64 / sc.wetland.len() as f64
                        },
                    },
                    "spike2_counts": {
                        "wetland_records": spike_records.len(),
                        "soft_records": s_soft,
                        "hard_records": s_hard,
                        "soft_share": if spike_records.is_empty() {
                            0.0
                        } else {
                            s_soft as f64 / spike_records.len() as f64
                        },
                    },
                    "ratio_release_vs_spike2_total": if spike_records.is_empty() {
                        0.0
                    } else {
                        sc.wetland.len() as f64 / spike_records.len() as f64
                    },
                    "class_soft": CLASS_SOFT,
                    "class_hard": CLASS_HARD,
                })
            );
        }
    }
    Ok(())
}
