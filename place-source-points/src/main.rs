//! CLI: place-source-points <in.osm.pbf> <out.navi-place-source.osm.pbf>
use std::path::PathBuf;
use std::time::Instant;

use anyhow::{bail, Context, Result};
use place_source_points::build_points_file;

fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let input = PathBuf::from(
        args.next()
            .context("usage: place-source-points <in.osm.pbf> <out.navi-place-source.osm.pbf>")?,
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
