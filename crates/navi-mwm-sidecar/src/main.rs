use std::path::PathBuf;

use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use navi_mwm_sidecar::{
    build_sidecar_bytes, load_boardwalk_jsonl, load_wetland_jsonl, read_sidecar, sha256_file,
    write_sidecar,
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
            let attr = std::fs::read_to_string(&attribution)
                .with_context(|| format!("read {}", attribution.display()))?;
            let bytes = build_sidecar_bytes(
                &generator_tag,
                &generator_sha,
                mwm_sha,
                mwm_size,
                wetland,
                boardwalk,
                &attr,
            )?;
            write_sidecar(&out, &bytes)?;
            let sc = read_sidecar(&out)?;
            println!(
                "{}",
                json!({
                    "out": out.display().to_string(),
                    "mwm_sha256": hex_encode(&sc.mwm_sha256),
                    "mwm_bytes": sc.mwm_size,
                    "wetland_records": sc.wetland.len(),
                    "boardwalk_records": sc.boardwalk.len(),
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
            println!(
                "{}",
                json!({
                    "format_version": sc.format_version,
                    "generator_tag": sc.generator_tag,
                    "generator_sha": sc.generator_sha,
                    "mwm_sha256": hex_encode(&sc.mwm_sha256),
                    "mwm_size": sc.mwm_size,
                    "wetland_records": sc.wetland.len(),
                    "boardwalk_records": sc.boardwalk.len(),
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
            println!(
                "{}",
                json!({"ok": true, "wetland_records": sc.wetland.len()})
            );
        }
    }
    Ok(())
}
