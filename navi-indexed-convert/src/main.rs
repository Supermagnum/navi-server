//! CLI: convert a region `.osm.pbf` into `.navi-graph-*.rkyv` + `.navi-poi-barrier.rkyv`.

use std::env;
use std::path::PathBuf;

use pack_convert_core::RoutingProfile;
use pack_convert_core::{convert_region_packs, ConvertOptions};
use pack_convert_core::WorkerPoolPlan;

fn main() {
    let plan = WorkerPoolPlan::detect();
    WorkerPoolPlan::lower_current_thread_priority();
    let _ = plan.install_rayon_pool();
    eprintln!(
        "workers detected_cores={} routing_workers={} tile_build_concurrency={} reserved={}",
        plan.detected_cores,
        plan.routing_workers,
        plan.tile_build_concurrency(),
        plan.reserved_for_ui_audio
    );

    let args: Vec<String> = env::args().collect();
    let mut data_dir = None;
    let mut pbf = None;
    let mut elev = None;
    let mut profiles = vec![RoutingProfile::Car, RoutingProfile::Foot];
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--data-dir" => {
                data_dir = Some(PathBuf::from(&args[i + 1]));
                i += 2;
            }
            "--pbf" => {
                pbf = Some(PathBuf::from(&args[i + 1]));
                i += 2;
            }
            "--elev-dir" => {
                elev = Some(PathBuf::from(&args[i + 1]));
                i += 2;
            }
            "--profiles" => {
                profiles = args[i + 1]
                    .split(',')
                    .map(|s| match s.trim() {
                        "car" => RoutingProfile::Car,
                        "truck" => RoutingProfile::Truck,
                        "foot" => RoutingProfile::Foot,
                        "bicycle" => RoutingProfile::Bicycle,
                        other => panic!("unknown profile {other}"),
                    })
                    .collect();
                i += 2;
            }
            other => panic!("unknown arg {other}"),
        }
    }
    let data_dir = data_dir.expect("--data-dir");
    let pbf = pbf.expect("--pbf");
    let mut opts = ConvertOptions::new(&data_dir, &pbf);
    opts.elev_dir = elev;
    opts.profiles = profiles;
    match convert_region_packs(&opts) {
        Ok(r) => {
            println!(
                "PASS stem={} convert_ms={:.1} bbox_scan_ms={:.1} graph_ms={:?} tile_assign_ms={:?} tile_build_ms={:?} poi_ms={:.1} barrier_ms={:.1} overnight_ms={:.1} wetland_ms={:.1} nodes={} edges={} pois={} barrier_segs={} wetland_rings={} graph_tiles={} peak_rss_mb={:.1} delta_h={} delta_h_missing_edges={} graphs={:?} poi={} wetland={} manifest={}",
                r.stem,
                r.convert_ms,
                r.bbox_scan_ms,
                r.graph_ms,
                r.tile_assign_ms,
                r.tile_build_ms,
                r.poi_ms,
                r.barrier_ms,
                r.overnight_ms,
                r.wetland_ms,
                r.nodes,
                r.edges,
                r.pois,
                r.barrier_segs,
                r.wetland_rings,
                r.graph_tiles,
                r.peak_rss_mb,
                r.has_delta_h,
                r.delta_h_missing_edges,
                r.graph_files,
                r.poi_barrier_file,
                r.wetland_file,
                r.manifest_file
            );
        }
        Err(e) => {
            eprintln!("FAIL: {e:#}");
            std::process::exit(1);
        }
    }
}
