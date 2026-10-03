//! Synthetic ferry terminal: car ferry lands on a pier/footway chain that was
//! dropped for the car profile before ferry-link promotion.

use std::fs;
use std::process::Command;

use pack_convert_core::routing::graph::RoutingProfile;

const OSM: &str = r#"<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6" generator="navi-ferry-boarding-test">
  <!-- Road network west of the terminal -->
  <node id="1" lat="60.0000" lon="5.0000"/>
  <node id="2" lat="60.0000" lon="5.0010"/>
  <!-- Footway + pier to the ferry berth -->
  <node id="3" lat="60.0000" lon="5.0015"/>
  <node id="4" lat="60.0000" lon="5.0020"/>
  <!-- Far shore: pier + footway + road -->
  <node id="5" lat="60.0000" lon="5.0100"/>
  <node id="6" lat="60.0000" lon="5.0105"/>
  <node id="7" lat="60.0000" lon="5.0110"/>
  <node id="8" lat="60.0000" lon="5.0120"/>
  <!-- Passenger-only ferry (must stay non-car) -->
  <node id="20" lat="60.0100" lon="5.0000"/>
  <node id="21" lat="60.0100" lon="5.0010"/>
  <node id="22" lat="60.0100" lon="5.0015"/>
  <node id="23" lat="60.0100" lon="5.0100"/>

  <way id="100">
    <nd ref="1"/><nd ref="2"/>
    <tag k="highway" v="primary"/>
    <tag k="name" v="Mainland Road"/>
  </way>
  <way id="110">
    <nd ref="2"/><nd ref="3"/>
    <tag k="highway" v="footway"/>
    <tag k="name" v="Terminal Footway"/>
  </way>
  <way id="120">
    <nd ref="3"/><nd ref="4"/>
    <tag k="man_made" v="pier"/>
    <tag k="name" v="Terminal Pier"/>
  </way>
  <way id="200">
    <nd ref="4"/><nd ref="5"/>
    <tag k="route" v="ferry"/>
    <tag k="motor_vehicle" v="yes"/>
    <tag k="duration" v="0:20"/>
    <tag k="name" v="Car Ferry Crossing"/>
  </way>
  <way id="130">
    <nd ref="5"/><nd ref="6"/>
    <tag k="man_made" v="pier"/>
    <tag k="name" v="Far Pier"/>
  </way>
  <way id="140">
    <nd ref="6"/><nd ref="7"/>
    <tag k="highway" v="platform"/>
    <tag k="name" v="Far Platform"/>
  </way>
  <way id="150">
    <nd ref="7"/><nd ref="8"/>
    <tag k="highway" v="primary"/>
    <tag k="name" v="Island Road"/>
  </way>

  <way id="300">
    <nd ref="20"/><nd ref="21"/>
    <tag k="highway" v="residential"/>
  </way>
  <way id="310">
    <nd ref="21"/><nd ref="22"/>
    <tag k="highway" v="footway"/>
  </way>
  <way id="320">
    <nd ref="22"/><nd ref="23"/>
    <tag k="route" v="ferry"/>
    <tag k="foot" v="yes"/>
    <tag k="name" v="Passenger Ferry"/>
  </way>
</osm>
"#;

fn which_osmium() -> Option<std::path::PathBuf> {
    Command::new("osmium")
        .arg("--version")
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|_| std::path::PathBuf::from("osmium"))
}

fn way_prefix(graph: &pack_convert_core::routing::graph::RouteGraph, way_id: &str) -> bool {
    let prefix = format!("{way_id}-");
    graph.edges.iter().any(|e| e.id.starts_with(&prefix))
}

#[test]
fn car_ferry_boarding_links_promoted_and_passenger_not() {
    let osmium = match which_osmium() {
        Some(p) => p,
        None => {
            eprintln!("skip: osmium not on PATH");
            return;
        }
    };
    let dir = std::env::temp_dir().join(format!("navi-ferry-board-{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    let osm = dir.join("terminal.osm");
    let pbf = dir.join("terminal.osm.pbf");
    fs::write(&osm, OSM).unwrap();
    assert!(Command::new(&osmium)
        .args(["cat", osm.to_str().unwrap(), "-o"])
        .arg(&pbf)
        .status()
        .unwrap()
        .success());

    let bbox = [59.99, 4.99, 60.02, 5.02];
    let car_off = pack_convert_core::routing::graph::RouteGraph::build_from_pbf_bbox(
        &pbf,
        RoutingProfile::Car,
        bbox,
    )
    .expect("car without ferry links");
    assert!(
        way_prefix(&car_off, "200"),
        "car ferry itself must still be present"
    );
    assert!(
        !way_prefix(&car_off, "110") && !way_prefix(&car_off, "120"),
        "without ferry_links, pier/footway must stay dropped for car"
    );
    assert!(
        !way_prefix(&car_off, "320"),
        "passenger ferry must stay out of car graph"
    );

    let car_on =
        pack_convert_core::routing::graph::RouteGraph::build_from_pbf_bbox_with_ferry_links(
            &pbf,
            RoutingProfile::Car,
            bbox,
            true,
        )
        .expect("car with ferry links");
    assert!(way_prefix(&car_on, "200"));
    assert!(
        way_prefix(&car_on, "110") && way_prefix(&car_on, "120"),
        "near-side boarding chain promoted"
    );
    assert!(
        way_prefix(&car_on, "130") && way_prefix(&car_on, "140"),
        "far-side boarding chain promoted"
    );
    assert!(
        !way_prefix(&car_on, "320") && !way_prefix(&car_on, "310"),
        "passenger ferry + its footway must not become car-routable"
    );

    // Mainland road -> island road must route across the car ferry.
    let (from, _) = car_on
        .nearest_routable(60.0000, 5.0000)
        .expect("snap mainland");
    let (to, _) = car_on
        .nearest_routable(60.0000, 5.0120)
        .expect("snap island");
    let (_nodes, edges, _cost) = car_on
        .shortest_path(from, to, false)
        .expect("routable with ferry links");
    assert!(
        edges
            .iter()
            .any(|&i| car_on.edges[i].id.starts_with("200-")),
        "route must use the car ferry"
    );

    let _ = fs::remove_dir_all(&dir);
}
