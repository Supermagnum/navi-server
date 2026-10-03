//! Snap two or more WGS84 points on a car pack and print the path, including ferry ways.
//!
//! Usage: ferry_route_check <pack-dir> lat,lon lat,lon [...]

use std::collections::BTreeSet;
use std::path::PathBuf;

use pack_convert_core::routing::graph::{way_id_from_edge_id, RouteGraph, RoutingProfile};
use pack_convert_core::routing::indexed::{load_graph_pack, merge_tile_graphs, NaviManifest};

fn main() {
    let mut args = std::env::args().skip(1);
    let Some(pack_dir) = args.next() else {
        eprintln!("usage: ferry_route_check <pack-dir> lat,lon lat,lon [...]");
        std::process::exit(2);
    };
    let pack_dir = PathBuf::from(pack_dir);
    let mut pts: Vec<(f64, f64)> = Vec::new();
    for a in args {
        let mut it = a.split(',');
        let lat: f64 = it.next().and_then(|s| s.parse().ok()).unwrap_or(f64::NAN);
        let lon: f64 = it.next().and_then(|s| s.parse().ok()).unwrap_or(f64::NAN);
        if !lat.is_finite() || !lon.is_finite() {
            eprintln!("bad point {a}");
            std::process::exit(2);
        }
        pts.push((lat, lon));
    }
    if pts.len() < 2 {
        eprintln!("need at least two lat,lon points");
        std::process::exit(2);
    }

    let graph = load_car_graph(&pack_dir).unwrap_or_else(|e| {
        eprintln!("FAIL load: {e:#}");
        std::process::exit(1);
    });

    let mut nodes = Vec::new();
    for (lat, lon) in &pts {
        match graph.nearest_routable(*lat, *lon) {
            Ok((id, dist)) => {
                println!(
                    "snap lat={lat:.5} lon={lon:.5} node={} dist_m={dist:.1}",
                    id.0
                );
                nodes.push(id);
            }
            Err(e) => {
                println!(
                    "UNREACHABLE snap lat={lat:.5} lon={lon:.5} nearest_m={:.1} max_m={:.1}",
                    e.nearest_m, e.max_m
                );
                std::process::exit(1);
            }
        }
    }

    for w in nodes.windows(2) {
        let (start, goal) = (w[0], w[1]);
        match graph.shortest_path(start, goal, false) {
            Some((_path_nodes, edges, cost)) => {
                let mut ferry_names = BTreeSet::new();
                let mut ferry_ways = BTreeSet::new();
                let mut ferry_n = 0usize;
                let mut denied_names = Vec::new();
                for &i in &edges {
                    let e = &graph.edges[i];
                    if !e.is_ferry {
                        continue;
                    }
                    ferry_n += 1;
                    if let Some(id) = way_id_from_edge_id(&e.id) {
                        ferry_ways.insert(id);
                    }
                    if let Some(n) = &e.name {
                        ferry_names.insert(n.clone());
                    }
                    if e.access_forbidden {
                        denied_names.push(e.name.clone().unwrap_or_default());
                    }
                }
                println!(
                    "ROUTE edges={} cost_m={cost:.1} ferry_edges={ferry_n} ferry_ways={} names={ferry_names:?}",
                    edges.len(),
                    ferry_ways.len()
                );
                for id in &ferry_ways {
                    println!("  ferry_way={id}");
                }
                if !denied_names.is_empty() {
                    println!("FAIL access_forbidden ferries on path: {denied_names:?}");
                    std::process::exit(1);
                }
            }
            None => {
                println!("NO_PATH {} -> {}", start.0, goal.0);
                std::process::exit(1);
            }
        }
    }
}

fn load_car_graph(pack_dir: &std::path::Path) -> anyhow::Result<RouteGraph> {
    let mans: Vec<_> = std::fs::read_dir(pack_dir)?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .and_then(|s| s.to_str())
                .is_some_and(|n| n.ends_with(".navi-manifest.json"))
        })
        .collect();
    let man_path = mans
        .first()
        .ok_or_else(|| anyhow::anyhow!("no *.navi-manifest.json in {}", pack_dir.display()))?;
    let man = NaviManifest::load(man_path)?;
    if man.uses_graph_tiles() {
        let tiles = man
            .graph_tiles_for(RoutingProfile::Car)
            .ok_or_else(|| anyhow::anyhow!("no car graph_tiles"))?;
        let mut graphs = Vec::with_capacity(tiles.len());
        for t in tiles {
            let path = pack_dir.join(&t.file);
            graphs.push(load_graph_pack(&path, RoutingProfile::Car)?);
        }
        Ok(merge_tile_graphs(graphs, RoutingProfile::Car))
    } else {
        let path = man
            .graph_path(pack_dir, RoutingProfile::Car)
            .ok_or_else(|| anyhow::anyhow!("no car graph_files"))?;
        Ok(load_graph_pack(&path, RoutingProfile::Car)?)
    }
}
