use std::path::PathBuf;

use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use navi_mwm_sidecar::{
    build_sidecar_bytes, load_boardwalk_jsonl, load_elevation_jsonl, load_wetland_jsonl,
    read_sidecar, read_spike2_flat_wetland_records, sha256_file, wetland_class_counts,
    write_sidecar, CLASS_HARD, CLASS_SOFT,
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

fn main() -> Result<()> {
    let cli = Cli::parse();
    match cli.cmd {
        Cmd::Bake {
            mwm,
            wetland_jsonl,
            boardwalk_jsonl,
            elevation_jsonl,
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
            let attr = std::fs::read_to_string(&attribution)
                .with_context(|| format!("read {}", attribution.display()))?;
            let bytes = build_sidecar_bytes(
                &generator_tag,
                &generator_sha,
                mwm_sha,
                mwm_size,
                wetland,
                boardwalk,
                elevation,
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
                    "sections": sc.sections.iter().map(|s| &s.name).collect::<Vec<_>>(),
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
