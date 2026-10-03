//! Audit car ferry landings for missing road connections.
//!
//! Distinguishes true boarding islands (no non-ferry incident edges, or a tiny
//! non-ferry component) from ferry landings that sit on a large secondary road
//! network (e.g. Stavanger-side of Boknafjorden) that is only joined to the
//! mainland giant via ferries. The latter are expected and not a Pass-1 drop.

use std::collections::{HashMap, HashSet, VecDeque};
use std::path::PathBuf;

use pack_convert_core::routing::graph::{GraphEdge, RouteGraph, RoutingProfile};
use pack_convert_core::routing::indexed::{load_graph_pack, merge_tile_graphs, NaviManifest};

/// Non-ferry undirected component smaller than this is treated as a boarding island.
const TINY_COMPONENT_NODES: usize = 50;

fn main() {
    let mut args = std::env::args().skip(1);
    let Some(pack_dir) = args.next() else {
        eprintln!(
            "usage: navi-ferry-terminal-audit <published-or-scratch-pack-dir>\n\
             Loads car graph tiles and classifies ferry endpoints:\n\
               no_road  — no non-ferry incident edge (true boarding gap)\n\
               tiny     — non-ferry component < {TINY_COMPONENT_NODES} nodes\n\
               other_comp — on a large road component that is not the giant\n\
                            (peninsula / island network joined only by ferry)"
        );
        std::process::exit(2);
    };
    let pack_dir = PathBuf::from(pack_dir);
    let graph = load_car_graph(&pack_dir).unwrap_or_else(|e| {
        eprintln!("FAIL load: {e:#}");
        std::process::exit(1);
    });
    let report = audit_ferry_terminals(&graph);
    println!(
        "car_edges={} ferry_edges={} ferry_endpoints_unique={} \
         no_road={} tiny_lt{TINY_COMPONENT_NODES}={} other_comp_not_giant={} giant_nodes={}",
        graph.edges.len(),
        report.ferry_edge_count,
        report.endpoint_count,
        report.no_road.len(),
        report.tiny.len(),
        report.other_comp.len(),
        report.giant_nodes
    );
    for t in &report.no_road {
        println!(
            "no_road name={:?} node={} lat={:.5} lon={:.5}",
            t.name.as_deref().unwrap_or(""),
            t.node,
            t.lat,
            t.lon
        );
    }
    for t in &report.tiny {
        println!(
            "tiny name={:?} node={} comp_nodes={} lat={:.5} lon={:.5}",
            t.name.as_deref().unwrap_or(""),
            t.node,
            t.comp_nodes,
            t.lat,
            t.lon
        );
    }
    if report.no_road.is_empty() && report.tiny.is_empty() {
        println!("OK: no true car-ferry boarding islands (no_road/tiny)");
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

#[derive(Debug)]
struct TerminalHit {
    node: i64,
    name: Option<String>,
    lat: f64,
    lon: f64,
    comp_nodes: usize,
}

#[derive(Debug)]
struct AuditReport {
    ferry_edge_count: usize,
    endpoint_count: usize,
    giant_nodes: usize,
    no_road: Vec<TerminalHit>,
    tiny: Vec<TerminalHit>,
    other_comp: Vec<TerminalHit>,
}

fn audit_ferry_terminals(graph: &RouteGraph) -> AuditReport {
    let mut adj: HashMap<i64, Vec<i64>> = HashMap::new();
    let mut ferry_edges: Vec<&GraphEdge> = Vec::new();
    for e in &graph.edges {
        if e.is_ferry {
            ferry_edges.push(e);
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

    let mut no_road = Vec::new();
    let mut tiny = Vec::new();
    let mut other_comp = Vec::new();
    let mut seen_ep: HashSet<i64> = HashSet::new();

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
            let hit = TerminalHit {
                node,
                name: e.name.clone(),
                lat,
                lon,
                comp_nodes,
            };
            if deg == 0 {
                no_road.push(hit);
            } else if comp_nodes < TINY_COMPONENT_NODES {
                tiny.push(hit);
            } else if giant_id.is_some_and(|g| comp_id != Some(g)) {
                other_comp.push(hit);
            }
        }
    }

    let sort_hits = |v: &mut Vec<TerminalHit>| {
        v.sort_by(|a, b| {
            (
                a.name.as_deref().unwrap_or(""),
                a.lat.to_bits(),
                a.lon.to_bits(),
                a.node,
            )
                .cmp(&(
                    b.name.as_deref().unwrap_or(""),
                    b.lat.to_bits(),
                    b.lon.to_bits(),
                    b.node,
                ))
        });
    };
    sort_hits(&mut no_road);
    sort_hits(&mut tiny);
    sort_hits(&mut other_comp);

    AuditReport {
        ferry_edge_count: ferry_edges.len(),
        endpoint_count: seen_ep.len(),
        giant_nodes,
        no_road,
        tiny,
        other_comp,
    }
}
