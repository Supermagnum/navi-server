//! CLI: place-source-points <in.osm.pbf> <out.navi-place-source.osm.pbf>
//!      place-source-points export-named-routes --input X --output Y [--bbox W,S,E,N]
use std::path::PathBuf;
use std::time::Instant;

use anyhow::{bail, Context, Result};
use place_source_points::{
    build_points_file, export_named_routes_jsonl_bbox, NamedRoutesBbox,
};

fn main() -> Result<()> {
    let mut args = std::env::args().skip(1).peekable();
    match args.peek().map(String::as_str) {
        Some("export-named-routes") => {
            args.next();
            let mut input: Option<PathBuf> = None;
            let mut output: Option<PathBuf> = None;
            let mut bbox: Option<NamedRoutesBbox> = None;
            while let Some(a) = args.next() {
                match a.as_str() {
                    "--input" => {
                        input = Some(PathBuf::from(
                            args.next().context("missing --input path")?,
                        ));
                    }
                    "--output" => {
                        output = Some(PathBuf::from(
                            args.next().context("missing --output path")?,
                        ));
                    }
                    "--bbox" => {
                        let raw = args.next().context("missing --bbox W,S,E,N")?;
                        let parts: Vec<f64> = raw
                            .split(',')
                            .map(|s| s.trim().parse::<f64>())
                            .collect::<std::result::Result<Vec<_>, _>>()
                            .context("--bbox needs west,south,east,north floats")?;
                        if parts.len() != 4 {
                            bail!("--bbox needs west,south,east,north");
                        }
                        bbox = Some(NamedRoutesBbox {
                            west: parts[0],
                            south: parts[1],
                            east: parts[2],
                            north: parts[3],
                        });
                    }
                    other => bail!("unknown argument: {other}"),
                }
            }
            let input = input.context(
                "usage: place-source-points export-named-routes --input X --output Y [--bbox W,S,E,N]",
            )?;
            let output = output.context("missing --output")?;
            if !input.is_file() {
                bail!("input missing: {}", input.display());
            }
            let t0 = Instant::now();
            let n = export_named_routes_jsonl_bbox(&input, &output, bbox)?;
            eprintln!(
                "wrote {} routes={} elapsed_sec={:.2}",
                output.display(),
                n,
                t0.elapsed().as_secs_f64(),
            );
            Ok(())
        }
        _ => {
            let input = PathBuf::from(
                args.next().context(
                    "usage: place-source-points <in.osm.pbf> <out.navi-place-source.osm.pbf>\n       place-source-points export-named-routes --input X --output Y",
                )?,
            );
            let output = PathBuf::from(args.next().context("missing output path")?);
            if !input.is_file() {
                bail!("input missing: {}", input.display());
            }
            let t0 = Instant::now();
            let built = build_points_file(&input, &output)?;
            eprintln!(
                "wrote {} bytes={} elapsed_sec={:.2} search_nodes={} search_ways={} routes={} admin_rels={} admin_ways={} geom_nodes={} stub_nodes={}",
                output.display(),
                std::fs::metadata(&output)?.len(),
                t0.elapsed().as_secs_f64(),
                built.search_nodes,
                built.search_ways,
                built.routes,
                built.admin_rels,
                built.admin_ways,
                built.geom_nodes,
                built.stub_nodes,
            );
            Ok(())
        }
    }
}
