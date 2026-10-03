//! Synthetic OSM: car ferry admission and weight, foot-only ferry, tunnel flag
//! round-trip through a v9 graph pack, and v8/v9 preamble rejection.

use std::fs;
use std::process::Command;

use pack_convert_core::routing::graph::{
    ferry_base_weight_m, GraphEdge, RouteGraph, RoutingProfile, FERRY_CAR_BOARDING_PENALTY_MIN,
    FERRY_DRIVE_EQUIV_KMH,
};
use pack_convert_core::routing::indexed::{
    load_graph_pack, write_archive_atomic, FlatGraphPack, PackLoadError, Preamble,
    GRAPH_FORMAT_VERSION, MAGIC_GRAPH,
};

const OSM: &str = r#"<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="navi-v9-test">
  <node id="1" lat="59.0000" lon="10.0000"/>
  <node id="2" lat="59.0000" lon="10.0100"/>
  <node id="3" lat="59.0100" lon="10.0100"/>
  <node id="4" lat="59.0100" lon="10.0200"/>
  <node id="5" lat="59.0200" lon="10.0000"/>
  <node id="6" lat="59.0200" lon="10.0050"/>
  <node id="7" lat="59.0200" lon="10.0150"/>
  <node id="8" lat="59.0200" lon="10.0200"/>
  <node id="10" lat="59.0050" lon="10.0300"/>
  <node id="11" lat="59.0050" lon="10.0310"/>
  <node id="12" lat="59.0050" lon="10.0320"/>
  <node id="13" lat="59.0050" lon="10.0330"/>
  <node id="14" lat="59.0050" lon="10.0340"/>
  <node id="30" lat="59.0100" lon="10.0250"/>
  <node id="31" lat="59.0100" lon="10.0350"/>
  <node id="32" lat="59.0100" lon="10.0450"/>
  <node id="33" lat="59.0100" lon="10.0480"/>
  <node id="21" lat="59.0150" lon="10.0310"/>
  <way id="100">
    <nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="primary"/>
  </way>
  <way id="200">
    <nd ref="2"/><nd ref="3"/>
    <tag k="route" v="ferry"/>
    <tag k="motor_vehicle" v="yes"/>
    <tag k="duration" v="0:10"/>
    <tag k="name" v="Test Car Ferry"/>
  </way>
  <way id="101">
    <nd ref="3"/><nd ref="4"/>
    <tag k="highway" v="primary"/>
  </way>
  <way id="110">
    <nd ref="5"/><nd ref="6"/>
    <tag k="highway" v="path"/>
    <tag k="foot" v="yes"/>
  </way>
  <way id="300">
    <nd ref="6"/><nd ref="7"/>
    <tag k="route" v="ferry"/>
    <tag k="foot" v="yes"/>
    <tag k="duration" v="0:10"/>
    <tag k="name" v="Test Foot Ferry"/>
  </way>
  <way id="111">
    <nd ref="7"/><nd ref="8"/>
    <tag k="highway" v="path"/>
    <tag k="foot" v="yes"/>
  </way>
  <way id="301">
    <nd ref="20"/><nd ref="21"/>
    <tag k="route" v="ferry"/>
    <tag k="access" v="no"/>
    <tag k="name" v="Closed Ferry"/>
  </way>
  <way id="400">
    <nd ref="10"/><nd ref="11"/>
    <tag k="highway" v="residential"/>
    <tag k="tunnel" v="yes"/>
    <tag k="name" v="Yes Tunnel"/>
  </way>
  <way id="401">
    <nd ref="11"/><nd ref="12"/>
    <tag k="highway" v="residential"/>
    <tag k="name" v="Open Road"/>
  </way>
  <way id="402">
    <nd ref="12"/><nd ref="13"/>
    <tag k="highway" v="residential"/>
    <tag k="tunnel" v="no"/>
    <tag k="name" v="Not A Tunnel"/>
  </way>
  <way id="403">
    <nd ref="13"/><nd ref="14"/>
    <tag k="highway" v="residential"/>
    <tag k="tunnel" v="building_passage"/>
    <tag k="name" v="Passage"/>
  </way>
  <way id="500">
    <nd ref="4"/><nd ref="30"/>
    <tag k="route" v="ferry"/>
    <tag k="ferry" v="secondary"/>
    <tag k="duration" v="PT10M"/>
    <tag k="name" v="Losna-Rutledal"/>
  </way>
  <way id="102">
    <nd ref="30"/><nd ref="31"/>
    <tag k="highway" v="primary"/>
  </way>
  <way id="600">
    <nd ref="31"/><nd ref="32"/>
    <tag k="route" v="ferry"/>
    <tag k="duration" v="15"/>
    <tag k="name" v="Inherited relation ferry"/>
  </way>
  <way id="103">
    <nd ref="32"/><nd ref="33"/>
    <tag k="highway" v="primary"/>
  </way>
  <relation id="9000">
    <member type="way" ref="600" role=""/>
    <tag k="type" v="route"/>
    <tag k="route" v="ferry"/>
    <tag k="motor_vehicle" v="yes"/>
    <tag k="name" v="Car route relation"/>
  </relation>
  <node id="40" lat="59.0220" lon="10.0400"/>
  <node id="41" lat="59.0220" lon="10.0410"/>
  <way id="700">
    <nd ref="40"/><nd ref="41"/>
    <tag k="route" v="ferry"/>
    <tag k="name" v="Kystruten test liner"/>
  </way>
  <relation id="9001">
    <member type="way" ref="700" role=""/>
    <member type="node" ref="1" role="stop"/>
    <member type="node" ref="2" role="stop"/>
    <member type="node" ref="3" role="stop"/>
    <member type="node" ref="4" role="stop"/>
    <member type="node" ref="5" role="stop"/>
    <member type="node" ref="6" role="stop"/>
    <member type="node" ref="7" role="stop"/>
    <member type="node" ref="8" role="stop"/>
    <tag k="type" v="route"/>
    <tag k="route" v="ferry"/>
    <tag k="motor_vehicle" v="yes"/>
    <tag k="name" v="Kystruten Bergen-Kirkenes"/>
  </relation>
</osm>
"#;

fn way_edges<'a>(graph: &'a RouteGraph, way_id: &str) -> Vec<&'a GraphEdge> {
    let prefix = format!("{way_id}-");
    graph
        .edges
        .iter()
        .filter(|e| e.id.starts_with(&prefix))
        .collect()
}

fn path_uses_way(graph: &RouteGraph, edge_idxs: &[usize], way_id: &str) -> bool {
    let prefix = format!("{way_id}-");
    edge_idxs
        .iter()
        .any(|&i| graph.edges[i].id.starts_with(&prefix))
}

#[test]
fn car_and_foot_ferries_and_tunnel_flags_roundtrip_v9() {
    let osmium = match which_osmium() {
        Some(p) => p,
        None => {
            eprintln!("skip: osmium not on PATH");
            return;
        }
    };
    let dir = std::env::temp_dir().join(format!("navi-v9-ferry-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).expect("tmpdir");
    let osm = dir.join("ferry.osm");
    let pbf = dir.join("ferry.osm.pbf");
    fs::write(&osm, OSM).expect("write osm");
    let status = Command::new(&osmium)
        .args(["cat", "-o"])
        .arg(&pbf)
        .arg(&osm)
        .status()
        .expect("spawn osmium");
    assert!(status.success(), "osmium cat failed: {status}");

    let bbox = [58.99, 9.99, 59.03, 10.05];
    let car = RouteGraph::build_from_pbf_bbox(&pbf, RoutingProfile::Car, bbox).expect("car graph");
    let foot =
        RouteGraph::build_from_pbf_bbox(&pbf, RoutingProfile::Foot, bbox).expect("foot graph");

    assert!(
        way_edges(&car, "300").is_empty(),
        "car graph must not contain the foot-only ferry"
    );
    assert!(
        way_edges(&car, "301").is_empty(),
        "car graph must not contain access=no ferry"
    );
    assert!(
        !way_edges(&car, "500").is_empty(),
        "car graph must admit ferry=secondary with no motor tags"
    );
    assert!(
        !way_edges(&car, "600").is_empty(),
        "car graph must admit untagged member of a car-capable route=ferry relation"
    );
    assert!(
        way_edges(&car, "700").is_empty(),
        "car graph must not inherit from a many-stop coastal liner relation"
    );
    let inherited = way_edges(&car, "500");
    let expected_iso =
        ferry_base_weight_m(inherited[0].length_m, Some("PT10M"), RoutingProfile::Car);
    assert!((inherited[0].base_weight - expected_iso).abs() < 1e-3);
    assert!(
        way_edges(&foot, "301").is_empty(),
        "foot graph must not contain access=no ferry when foot is unset"
    );

    let car_ferry = way_edges(&car, "200");
    assert_eq!(car_ferry.len(), 2, "car ferry is bidirectional");
    let mps = FERRY_DRIVE_EQUIV_KMH * 1000.0 / 3600.0;
    let expected = ferry_base_weight_m(car_ferry[0].length_m, Some("0:10"), RoutingProfile::Car);
    let boarding = FERRY_CAR_BOARDING_PENALTY_MIN * 60.0 * mps;
    for edge in &car_ferry {
        assert!(edge.is_ferry);
        assert!(!edge.is_tunnel);
        assert!(
            (edge.base_weight - expected).abs() < 1e-3,
            "base_weight {} expected {expected}",
            edge.base_weight
        );
        assert!(edge.base_weight > edge.length_m + boarding - 1.0);
        assert!(edge.length_m > 100.0 && edge.length_m < 5_000.0);
    }

    let (start, _) = car.nearest_routable(59.0000, 10.0000).expect("snap start");
    let (goal, _) = car.nearest_routable(59.0100, 10.0200).expect("snap goal");
    let (_nodes, edges, _cost) = car
        .shortest_path(start, goal, false)
        .expect("car route across the ferry");
    assert!(
        path_uses_way(&car, &edges, "200"),
        "car route must use the car ferry"
    );
    assert!(!path_uses_way(&car, &edges, "300"));

    let foot_ferry = way_edges(&foot, "300");
    assert_eq!(foot_ferry.len(), 2, "foot ferry is bidirectional");
    let foot_expected =
        ferry_base_weight_m(foot_ferry[0].length_m, Some("0:10"), RoutingProfile::Foot);
    for edge in &foot_ferry {
        assert!(edge.is_ferry);
        assert!((edge.base_weight - foot_expected).abs() < 1e-3);
        assert!((edge.base_weight - (600.0 * mps)).abs() < 1.0);
    }
    let (f0, _) = foot.nearest_routable(59.0200, 10.0000).expect("foot snap");
    let (f1, _) = foot.nearest_routable(59.0200, 10.0200).expect("foot snap");
    let (_n, f_edges, _) = foot
        .shortest_path(f0, f1, false)
        .expect("foot route across the foot ferry");
    assert!(path_uses_way(&foot, &f_edges, "300"));

    assert_tunnel(&car, "400", true);
    assert_tunnel(&car, "401", false);
    assert_tunnel(&car, "402", false);
    assert_tunnel(&car, "403", true);

    let pack = FlatGraphPack::from_route_graph(&car, None);
    assert_eq!(pack.edge_is_tunnel.len(), car.edges.len());
    assert_eq!(pack.edge_is_ferry.len(), car.edges.len());
    let payload = rkyv::to_bytes::<rkyv::rancor::Error>(&pack).expect("serialize");
    let pack_path = dir.join("car.rkyv");
    write_archive_atomic(
        &pack_path,
        Preamble::new(MAGIC_GRAPH, GRAPH_FORMAT_VERSION),
        &payload,
    )
    .expect("write pack");
    let loaded = load_graph_pack(&pack_path, RoutingProfile::Car).expect("load v9");
    assert_eq!(loaded.edges.len(), car.edges.len());
    for (a, b) in car.edges.iter().zip(loaded.edges.iter()) {
        assert_eq!(a.is_tunnel, b.is_tunnel, "tunnel flag dropped on {}", a.id);
        assert_eq!(a.is_ferry, b.is_ferry);
        assert!((a.base_weight - b.base_weight).abs() < 1e-6);
        assert!((a.length_m - b.length_m).abs() < 1e-6);
    }

    let mut v8 = fs::read(&pack_path).expect("read pack");
    v8[4..8].copy_from_slice(&8u32.to_le_bytes());
    let v8_path = dir.join("car-v8.rkyv");
    fs::write(&v8_path, &v8).expect("write v8");
    match load_graph_pack(&v8_path, RoutingProfile::Car) {
        Err(PackLoadError::VersionMismatch) => {}
        Err(err) => panic!("v9 loader must reject a v8 preamble, got {err}"),
        Ok(_) => panic!("v9 loader must reject a v8 preamble"),
    }

    let _ = fs::remove_dir_all(&dir);
}

fn assert_tunnel(graph: &RouteGraph, way_id: &str, tunnel: bool) {
    let edges = way_edges(graph, way_id);
    assert!(!edges.is_empty(), "missing way {way_id}");
    for edge in edges {
        assert_eq!(edge.is_tunnel, tunnel, "way {way_id} edge {}", edge.id);
    }
}

fn which_osmium() -> Option<std::path::PathBuf> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join("osmium");
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}
