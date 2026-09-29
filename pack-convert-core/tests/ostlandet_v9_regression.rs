//! Ignored Ostlandet checks. They scan the full extract, so `cargo test` skips
//! them unless `-- --ignored` is passed. Missing PBF is a skip, not a failure.

use std::path::PathBuf;

use pack_convert_core::routing::graph::{RouteGraph, RoutingProfile};

fn ostlandet_pbf() -> Option<PathBuf> {
    let candidates = [
        PathBuf::from(
            "/media/navi/navi-server/data/scratch/extracts/europe_norway_ostlandet-latest.osm.pbf",
        ),
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("target/integration-fixtures/ostlandet-latest.osm.pbf"),
    ];
    candidates.into_iter().find(|p| p.is_file())
}

fn path_km(graph: &RouteGraph, edge_idxs: &[usize]) -> f64 {
    edge_idxs
        .iter()
        .map(|&i| graph.edges[i].length_m)
        .sum::<f64>()
        / 1000.0
}

fn route_km(graph: &RouteGraph, a: (f64, f64), b: (f64, f64)) -> f64 {
    let (start, _) = graph
        .nearest_routable(a.0, a.1)
        .unwrap_or_else(|e| panic!("snap {:?}: {e:?}", a));
    let (goal, _) = graph
        .nearest_routable(b.0, b.1)
        .unwrap_or_else(|e| panic!("snap {:?}: {e:?}", b));
    let (_nodes, edges, _) = graph
        .shortest_path(start, goal, false)
        .unwrap_or_else(|| panic!("no route {a:?} -> {b:?}"));
    path_km(graph, &edges)
}

#[test]
#[ignore = "scans the Ostlandet PBF"]
fn vestheim_r4b_and_espa_lengths() {
    let Some(pbf) = ostlandet_pbf() else {
        eprintln!("skip: ostlandet pbf not found");
        return;
    };
    // Published v8 car paths (generation 20260928):
    // R4b  lat 60.726..61.164 lon 10.610..11.509
    // Espa lat 60.562..61.851 lon 10.051..11.310
    // The west edge must stay left of lon 10.051. A cut at 10.15 drops the
    // last Espa approach and the geometric length becomes about 195.7 km.
    let bbox = [60.48, 9.95, 61.95, 11.60];
    let graph = RouteGraph::build_from_pbf_bbox(&pbf, RoutingProfile::Car, bbox).expect("build");

    let vestheim = graph.edges.iter().any(|e| {
        e.source.0 == 9_723_990_894
            && e.target.0 == 3_339_483_752
            && e.highway.as_deref() == Some("motorway")
            && e.name.as_deref().is_some_and(|n| n.contains("Vestheim"))
    });
    assert!(
        vestheim,
        "t2_3 corridor must keep motorway edge 9723990894 -> 3339483752 Vestheim bru"
    );

    let r4b_a = route_km(&graph, (60.7278503, 10.6109705), (60.821469, 11.200060));
    let r4b_b = route_km(&graph, (60.821469, 11.200060), (61.1638011, 11.4539336));
    let r4b = r4b_a + r4b_b;
    assert!(
        (r4b - 118.973).abs() < 5.0,
        "R4b expected ~118.973 km, got {r4b:.3} ({r4b_a:.3}+{r4b_b:.3})"
    );

    let espa = route_km(&graph, (60.5621914, 11.2561239), (61.8512500, 10.2338420));
    assert!(
        (espa - 189.041).abs() < 5.0,
        "Espa -> Atnbrua expected ~189.041 km, got {espa:.3}"
    );
}

#[test]
#[ignore = "scans the Ostlandet PBF"]
fn moss_horten_car_route_uses_ferry() {
    let Some(pbf) = ostlandet_pbf() else {
        eprintln!("skip: ostlandet pbf not found");
        return;
    };
    let bbox = [59.30, 10.30, 59.55, 10.80];
    let graph = RouteGraph::build_from_pbf_bbox(&pbf, RoutingProfile::Car, bbox).expect("build");
    let ferry: Vec<_> = graph
        .edges
        .iter()
        .filter(|e| e.id.starts_with("228431771-") && e.is_ferry)
        .collect();
    assert!(
        !ferry.is_empty(),
        "way 228431771 Horten - Moss must be a car ferry edge"
    );
    let edge = ferry[0];
    assert!(edge.base_weight > edge.length_m);
    let (_nodes, edges, _) = graph
        .shortest_path(edge.source, edge.target, false)
        .expect("route across the Moss-Horten ferry");
    assert!(
        edges.iter().any(|&i| graph.edges[i].is_ferry),
        "Moss-Horten route must use a ferry edge"
    );
}
