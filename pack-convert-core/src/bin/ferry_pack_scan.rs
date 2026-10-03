//! Inventory car-pack ferry edges + terminal audit (no_road / tiny).
//!
//! Usage: ferry_pack_scan <pack-dir> [--json-out PATH]
//! Prints a one-line summary to stdout; optional JSON detail for analysis.

use std::collections::{HashMap, HashSet, VecDeque};
use std::path::{Path, PathBuf};

use pack_convert_core::routing::graph::{way_id_from_edge_id, GraphEdge, RouteGraph, RoutingProfile};
use pack_convert_core::routing::indexed::{load_graph_pack, merge_tile_graphs, NaviManifest};
use serde::Serialize;

const TINY_COMPONENT_NODES: usize = 50;

#[derive(Debug, Serialize)]
struct FerryEdgeOut {
    edge_id: String,
    osm_way_id: Option<i64>,
    name: Option<String>,
    start_lat: f64,
    start_lon: f64,
    end_lat: f64,
    end_lon: f64,
    length_m: f64,
}

#[derive(Debug, Serialize)]
struct TerminalOut {
    kind: String,
    name: Option<String>,
    node: i64,
    lat: f64,
    lon: f64,
    comp_nodes: usize,
}

#[derive(Debug, Serialize)]
struct ScanReport {
    pack_dir: String,
    car_edges: usize,
    ferry_edges: usize,
    ferry_endpoints_unique: usize,
    no_road: usize,
    tiny: usize,
    other_comp: usize,
    giant_nodes: usize,
    ferries: Vec<FerryEdgeOut>,
    terminals: Vec<TerminalOut>,
}

fn main() {
    let mut args = std::env::args().skip(1);
    let Some(pack_dir) = args.next() else {
        eprintln!("usage: ferry_pack_scan <pack-dir> [--json-out PATH]");
        std::process::exit(2);
    };
    let pack_dir = PathBuf::from(pack_dir);
    let mut json_out: Option<PathBuf> = None;
    while let Some(a) = args.next() {
        if a == "--json-out" {
            json_out = args.next().map(PathBuf::from);
        }
    }

    let graph = load_car_graph(&pack_dir).unwrap_or_else(|e| {
        eprintln!("FAIL load: {e:#}");
        std::process::exit(1);
    });
    let report = scan(&pack_dir, &graph);

    println!(
        "car_edges={} ferry_edges={} ferry_endpoints_unique={} no_road={} tiny_lt{TINY_COMPONENT_NODES}={} other_comp_not_giant={} giant_nodes={}",
        report.car_edges,
        report.ferry_edges,
        report.ferry_endpoints_unique,
        report.no_road,
        report.tiny,
        report.other_comp,
        report.giant_nodes
    );

    if let Some(path) = json_out {
        let text = serde_json::to_string(&report).unwrap_or_else(|e| {
            eprintln!("FAIL json: {e}");
            std::process::exit(1);
        });
        if let Err(e) = std::fs::write(&path, text) {
            eprintln!("FAIL write {}: {e}", path.display());
            std::process::exit(1);
        }
    }
}

fn load_car_graph(pack_dir: &Path) -> anyhow::Result<RouteGraph> {
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

fn scan(pack_dir: &Path, graph: &RouteGraph) -> ScanReport {
    let mut ferries = Vec::new();
    let mut adj: HashMap<i64, Vec<i64>> = HashMap::new();
    let mut ferry_edges: Vec<&GraphEdge> = Vec::new();
    for e in &graph.edges {
        if e.is_ferry {
            ferry_edges.push(e);
            ferries.push(FerryEdgeOut {
                edge_id: e.id.clone(),
                osm_way_id: way_id_from_edge_id(&e.id),
                name: e.name.clone(),
                start_lat: e.start_lat,
                start_lon: e.start_lon,
                end_lat: e.end_lat,
                end_lon: e.end_lon,
                length_m: e.length_m,
            });
            continue;
        }
        adj.entry(e.source.0).or_default().push(e.target.0);
        adj.entry(e.target.0).or_default().push(e.source.0);
    }

    let mut comp_of: HashMap<i64, usize> = HashMap::new();
    let mut sizes: Vec<usize> = Vec::new();
    let mut seen: HashSet<i64> = HashSet::new();
    for &start in adj.keys() {
        if !seen.insert(start) {
            continue;
        }
        let id = sizes.len();
        let mut q = VecDeque::new();
        q.push_back(start);
        let mut sz = 0usize;
        while let Some(n) = q.pop_front() {
            comp_of.insert(n, id);
            sz += 1;
            for &m in adj.get(&n).into_iter().flatten() {
                if seen.insert(m) {
                    q.push_back(m);
                }
            }
        }
        sizes.push(sz);
    }
    let giant_id = sizes
        .iter()
        .enumerate()
        .max_by_key(|(_, &s)| s)
        .map(|(i, _)| i);
    let giant_nodes = giant_id.map(|i| sizes[i]).unwrap_or(0);

    let mut terminals = Vec::new();
    let mut seen_ep: HashSet<i64> = HashSet::new();
    let mut no_road = 0usize;
    let mut tiny = 0usize;
    let mut other_comp = 0usize;

    for e in &ferry_edges {
        for (node, lat, lon) in [
            (e.source.0, e.start_lat, e.start_lon),
            (e.target.0, e.end_lat, e.end_lon),
        ] {
            if !seen_ep.insert(node) {
                continue;
            }
            let deg = adj.get(&node).map(|v| v.len()).unwrap_or(0);
            let (comp_id, comp_nodes) = match comp_of.get(&node) {
                Some(&id) => (Some(id), sizes[id]),
                None => (None, 0),
            };
            let kind = if deg == 0 {
                no_road += 1;
                "no_road"
            } else if comp_nodes < TINY_COMPONENT_NODES {
                tiny += 1;
                "tiny"
            } else if giant_id.is_some_and(|g| comp_id != Some(g)) {
                other_comp += 1;
                "other_comp"
            } else {
                continue;
            };
            terminals.push(TerminalOut {
                kind: kind.to_string(),
                name: e.name.clone(),
                node,
                lat,
                lon,
                comp_nodes,
            });
        }
    }

    ScanReport {
        pack_dir: pack_dir.display().to_string(),
        car_edges: graph.edges.len(),
        ferry_edges: ferry_edges.len(),
        ferry_endpoints_unique: seen_ep.len(),
        no_road,
        tiny,
        other_comp,
        giant_nodes,
        ferries,
        terminals,
    }
}
