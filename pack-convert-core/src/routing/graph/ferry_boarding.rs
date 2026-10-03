//! Bounded car ferry terminal boarding links.
//!
//! OSM often connects `route=ferry` endpoints to the road network only via
//! `man_made=pier` / `highway=footway|platform` chains. Pass 1 drops those for
//! the car profile, leaving ferry landings as directed-graph islands.
//!
//! When enabled, we retain those candidate ways and promote only the short
//! undirected chains that connect a **car-capable** ferry endpoint to a
//! car-drivable road node that sits on a **non-stub** road component (length-
//! bounded). Attaching to a disconnected stub would inflate the audit `tiny`
//! class without making the landing useful for routing.

use std::collections::{HashMap, HashSet, VecDeque};
use std::hash::BuildHasher;

use super::builder::{ferry_allowed_for_profile, tags_indicate_ferry, RoutingProfile};

fn car_highway_ok(highway: &str) -> bool {
    matches!(
        highway,
        "motorway"
            | "motorway_link"
            | "motorway_junction"
            | "trunk"
            | "trunk_link"
            | "primary"
            | "primary_link"
            | "secondary"
            | "secondary_link"
            | "tertiary"
            | "tertiary_link"
            | "unclassified"
            | "residential"
            | "living_street"
            | "road"
            | "service"
            | "track"
    )
}

/// Maximum undirected length (metres) of pier/footway/platform chain from a
/// car-ferry endpoint to the first car-drivable road node.
pub const FERRY_BOARDING_MAX_CHAIN_M: f64 = 500.0;

/// Minimum car-drivable component size (**junction** node count) that counts as
/// a real road network rather than a disconnected stub. Matches the
/// ferry-terminal audit `tiny` band (<50 non-ferry graph nodes).
///
/// When every car-road component in the extract is smaller than this (unit /
/// fixture graphs), the threshold falls back to the largest component size so
/// legitimate short chains still promote.
pub const FERRY_BOARDING_MIN_ROAD_COMPONENT_NODES: usize = 50;

/// Haversine used only for chain length accounting (same formula as bbox_build).
fn haversine_m(lat1: f64, lon1: f64, lat2: f64, lon2: f64) -> f64 {
    const R: f64 = 6_371_000.0;
    let (lat1, lon1, lat2, lon2) = (
        lat1.to_radians(),
        lon1.to_radians(),
        lat2.to_radians(),
        lon2.to_radians(),
    );
    let dlat = lat2 - lat1;
    let dlon = lon2 - lon1;
    let a = (dlat / 2.0).sin().powi(2) + lat1.cos() * lat2.cos() * (dlon / 2.0).sin().powi(2);
    2.0 * R * a.sqrt().asin()
}

/// Ways that may bridge a car ferry landing to the road network.
pub fn is_ferry_boarding_candidate<S: BuildHasher>(tags: &HashMap<String, String, S>) -> bool {
    if tags_indicate_ferry(tags) {
        return false;
    }
    if tags
        .get("man_made")
        .is_some_and(|v| v.eq_ignore_ascii_case("pier"))
    {
        return true;
    }
    matches!(
        tags.get("highway").map(String::as_str),
        Some("footway") | Some("platform")
    )
}

/// Car-drivable OSM highway (not a ferry, not a boarding candidate).
pub fn is_car_drivable_road<S: BuildHasher>(tags: &HashMap<String, String, S>) -> bool {
    if tags_indicate_ferry(tags) {
        return false;
    }
    tags.get("highway").is_some_and(|h| car_highway_ok(h))
}

/// Car-capable ferry (`motor_vehicle` / `motorcar` yes).
pub fn is_car_capable_ferry<S: BuildHasher>(tags: &HashMap<String, String, S>) -> bool {
    tags_indicate_ferry(tags) && ferry_allowed_for_profile(tags, RoutingProfile::Car)
}

/// Way geometry for boarding promotion (id + node chain + tags).
pub struct BoardingWayRef<'a> {
    pub id: i64,
    pub nodes: &'a [i64],
    pub tags: &'a HashMap<String, String>,
}

/// Nodes on car-drivable ways whose undirected component is large enough to
/// count as a real network (not a pier-side stub).
///
/// Component size counts **junction nodes** (the same `uses > 1` rule as
/// [`graph_from_raw_ways`]), not raw OSM shape nodes. A long single way has
/// only two junctions; counting every shape node previously let island stubs
/// pass the 50-node gate and then inflate the ferry-terminal audit `tiny`
/// class after graph simplification.
fn substantial_road_nodes(ways: &[BoardingWayRef<'_>], min_component_nodes: usize) -> HashSet<i64> {
    let mut road_adj: HashMap<i64, Vec<i64>> = HashMap::new();
    let mut all_road_nodes: HashSet<i64> = HashSet::new();
    let mut uses: HashMap<i64, i32> = HashMap::new();

    for w in ways {
        if !is_car_drivable_road(w.tags) {
            continue;
        }
        let n = w.nodes.len();
        for (i, &id) in w.nodes.iter().enumerate() {
            all_road_nodes.insert(id);
            // Match graph_from_raw_ways junction scoring: endpoints count as 2.
            let add = if i == 0 || i + 1 == n { 2 } else { 1 };
            *uses.entry(id).or_insert(0) += add;
        }
        for pair in w.nodes.windows(2) {
            let (a, b) = (pair[0], pair[1]);
            if a == b {
                continue;
            }
            road_adj.entry(a).or_default().push(b);
            road_adj.entry(b).or_default().push(a);
        }
    }

    if all_road_nodes.is_empty() {
        return HashSet::new();
    }

    let is_junction = |id: i64| uses.get(&id).copied().unwrap_or(0) > 1;

    let mut node_comp_size: HashMap<i64, usize> = HashMap::new();
    let mut seen: HashSet<i64> = HashSet::new();
    let mut max_comp = 0usize;

    for &start in &all_road_nodes {
        if seen.contains(&start) {
            continue;
        }
        let mut comp: Vec<i64> = Vec::new();
        let mut q = VecDeque::new();
        q.push_back(start);
        seen.insert(start);
        while let Some(n) = q.pop_front() {
            comp.push(n);
            for &m in road_adj.get(&n).into_iter().flatten() {
                if seen.insert(m) {
                    q.push_back(m);
                }
            }
        }
        // Junction count matches ferry_terminal_audit component sizing.
        let sz = comp.iter().filter(|&&n| is_junction(n)).count();
        max_comp = max_comp.max(sz);
        for n in comp {
            node_comp_size.insert(n, sz);
        }
    }

    // Tiny fixture extracts: fall back to the largest component so short
    // legitimate chains still promote. Region-scale graphs keep the absolute
    // stub threshold (50).
    let threshold = min_component_nodes.min(max_comp).max(1);
    all_road_nodes
        .into_iter()
        .filter(|n| node_comp_size.get(n).copied().unwrap_or(0) >= threshold)
        .collect()
}

/// Returns the set of boarding-candidate way ids that lie on a bounded path
/// from a car-ferry endpoint node to a car-drivable road node on a non-stub
/// road component.
pub fn promote_ferry_boarding_way_ids(
    ways: &[BoardingWayRef<'_>],
    coords: &HashMap<i64, (f64, f64)>,
    max_chain_m: f64,
) -> HashSet<i64> {
    promote_ferry_boarding_way_ids_with_min_road(
        ways,
        coords,
        max_chain_m,
        FERRY_BOARDING_MIN_ROAD_COMPONENT_NODES,
    )
}

/// Same as [`promote_ferry_boarding_way_ids`] with an explicit minimum road
/// component size (for tests).
pub fn promote_ferry_boarding_way_ids_with_min_road(
    ways: &[BoardingWayRef<'_>],
    coords: &HashMap<i64, (f64, f64)>,
    max_chain_m: f64,
    min_road_component_nodes: usize,
) -> HashSet<i64> {
    let mut candidate_ids: HashSet<i64> = HashSet::new();
    let mut ferry_endpoint_nodes: HashSet<i64> = HashSet::new();
    let road_nodes = substantial_road_nodes(ways, min_road_component_nodes);

    // undirected adjacency: node -> [(neighbor, way_id, segment_length_m)]
    let mut adj: HashMap<i64, Vec<(i64, i64, f64)>> = HashMap::new();

    for w in ways {
        if is_car_capable_ferry(w.tags) {
            if let (Some(&a), Some(&b)) = (w.nodes.first(), w.nodes.last()) {
                ferry_endpoint_nodes.insert(a);
                ferry_endpoint_nodes.insert(b);
            }
            continue;
        }
        if is_car_drivable_road(w.tags) {
            continue;
        }
        if !is_ferry_boarding_candidate(w.tags) {
            continue;
        }
        candidate_ids.insert(w.id);
        for pair in w.nodes.windows(2) {
            let (a, b) = (pair[0], pair[1]);
            let Some(&(lat1, lon1)) = coords.get(&a) else {
                continue;
            };
            let Some(&(lat2, lon2)) = coords.get(&b) else {
                continue;
            };
            let len = haversine_m(lat1, lon1, lat2, lon2);
            if len <= 0.0 {
                continue;
            }
            adj.entry(a).or_default().push((b, w.id, len));
            adj.entry(b).or_default().push((a, w.id, len));
        }
    }

    if ferry_endpoint_nodes.is_empty() || candidate_ids.is_empty() || road_nodes.is_empty() {
        return HashSet::new();
    }

    let mut promoted: HashSet<i64> = HashSet::new();

    // BFS from each ferry endpoint over candidate edges only.
    for &start in &ferry_endpoint_nodes {
        if road_nodes.contains(&start) {
            // Ferry already touches a car road — no boarding chain needed.
            continue;
        }
        let mut queue: VecDeque<(i64, f64)> = VecDeque::new();
        let mut best_dist: HashMap<i64, f64> = HashMap::new();
        // parent_node -> (prev_node, via_way_id)
        let mut parent: HashMap<i64, (i64, i64)> = HashMap::new();
        queue.push_back((start, 0.0));
        best_dist.insert(start, 0.0);
        let mut found: Option<i64> = None;
        while let Some((node, dist)) = queue.pop_front() {
            if road_nodes.contains(&node) && node != start {
                found = Some(node);
                break;
            }
            let Some(edges) = adj.get(&node) else {
                continue;
            };
            for &(next, way_id, seg_len) in edges {
                let nd = dist + seg_len;
                if nd > max_chain_m {
                    continue;
                }
                if best_dist.get(&next).is_some_and(|&d| d <= nd) {
                    continue;
                }
                best_dist.insert(next, nd);
                parent.insert(next, (node, way_id));
                queue.push_back((next, nd));
            }
        }
        let Some(goal) = found else {
            continue;
        };
        let mut cur = goal;
        while cur != start {
            let Some(&(prev, way_id)) = parent.get(&cur) else {
                break;
            };
            if candidate_ids.contains(&way_id) {
                promoted.insert(way_id);
            }
            cur = prev;
        }
    }

    promoted
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tags(pairs: &[(&str, &str)]) -> HashMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| ((*k).to_string(), (*v).to_string()))
            .collect()
    }

    #[test]
    fn promotes_short_pier_footway_chain_only() {
        // Road -- footway -- pier -- ferry -- (far side omitted)
        // nodes: 1-2 road, 2-3 footway, 3-4 pier, 4-5 ferry
        let road = tags(&[("highway", "primary")]);
        let foot = tags(&[("highway", "footway")]);
        let pier = tags(&[("man_made", "pier")]);
        let ferry = tags(&[("route", "ferry"), ("motor_vehicle", "yes")]);
        let stray = tags(&[("highway", "footway")]);

        let n1 = 1i64;
        let n2 = 2;
        let n3 = 3;
        let n4 = 4;
        let n5 = 5;
        // ~50 m steps east
        let coords: HashMap<i64, (f64, f64)> = [
            (n1, (60.0, 5.0)),
            (n2, (60.0, 5.0005)),
            (n3, (60.0, 5.0010)),
            (n4, (60.0, 5.0015)),
            (n5, (60.0, 5.0020)),
            // stray footway far away
            (10, (61.0, 5.0)),
            (11, (61.0, 5.0005)),
        ]
        .into_iter()
        .collect();

        let road_nodes = [n1, n2];
        let foot_nodes = [n2, n3];
        let pier_nodes = [n3, n4];
        let ferry_nodes = [n4, n5];
        let stray_nodes = [10, 11];

        let ways = [
            BoardingWayRef {
                id: 100,
                nodes: &road_nodes,
                tags: &road,
            },
            BoardingWayRef {
                id: 200,
                nodes: &foot_nodes,
                tags: &foot,
            },
            BoardingWayRef {
                id: 300,
                nodes: &pier_nodes,
                tags: &pier,
            },
            BoardingWayRef {
                id: 400,
                nodes: &ferry_nodes,
                tags: &ferry,
            },
            BoardingWayRef {
                id: 999,
                nodes: &stray_nodes,
                tags: &stray,
            },
        ];

        let promoted = promote_ferry_boarding_way_ids(&ways, &coords, FERRY_BOARDING_MAX_CHAIN_M);
        assert!(promoted.contains(&200), "footway link promoted");
        assert!(promoted.contains(&300), "pier link promoted");
        assert!(!promoted.contains(&999), "unrelated footway not promoted");
        assert!(!promoted.contains(&100));
        assert!(!promoted.contains(&400));
    }

    #[test]
    fn passenger_ferry_does_not_promote_links() {
        let road = tags(&[("highway", "primary")]);
        let foot = tags(&[("highway", "footway")]);
        let ferry = tags(&[("route", "ferry"), ("foot", "yes")]); // no motor_vehicle
        let coords: HashMap<i64, (f64, f64)> = [
            (1, (60.0, 5.0)),
            (2, (60.0, 5.0005)),
            (3, (60.0, 5.0010)),
            (4, (60.0, 5.0015)),
        ]
        .into_iter()
        .collect();
        let road_n = [1i64, 2];
        let foot_n = [2, 3];
        let ferry_n = [3, 4];
        let ways = [
            BoardingWayRef {
                id: 1,
                nodes: &road_n,
                tags: &road,
            },
            BoardingWayRef {
                id: 2,
                nodes: &foot_n,
                tags: &foot,
            },
            BoardingWayRef {
                id: 3,
                nodes: &ferry_n,
                tags: &ferry,
            },
        ];
        let promoted = promote_ferry_boarding_way_ids(&ways, &coords, FERRY_BOARDING_MAX_CHAIN_M);
        assert!(promoted.is_empty());
    }

    #[test]
    fn does_not_promote_chain_that_only_reaches_road_stub() {
        // Junction-rich main network far from the ferry, plus a 2-junction stub
        // next to the pier. Ferry -- pier -- stub must NOT promote.
        let main = tags(&[("highway", "primary")]);
        let stub = tags(&[("highway", "service")]);
        let pier = tags(&[("man_made", "pier")]);
        let ferry = tags(&[("route", "ferry"), ("motor_vehicle", "yes")]);

        let stub_n = [20i64, 21];
        let pier_n = [21i64, 22];
        let ferry_n = [22i64, 23];

        let mut coords: HashMap<i64, (f64, f64)> = [
            (20, (60.0, 5.0005)),
            (21, (60.0, 5.0010)),
            (22, (60.0, 5.0015)),
            (23, (60.0, 5.0020)),
        ]
        .into_iter()
        .collect();

        // Distant hub+spokes: 6 junctions (hub + 5 leaves) so min=3 accepts
        // main and rejects the 2-junction stub.
        let hub_far = 10i64;
        coords.insert(hub_far, (60.1, 5.0));
        let mut far_spokes: Vec<Vec<i64>> = Vec::new();
        for i in 0..5 {
            let leaf = 11 + i;
            coords.insert(leaf, (60.1 + i as f64 * 0.0001, 5.0));
            far_spokes.push(vec![hub_far, leaf]);
        }
        let mut ways: Vec<BoardingWayRef<'_>> = far_spokes
            .iter()
            .enumerate()
            .map(|(i, nodes)| BoardingWayRef {
                id: 10 + i as i64,
                nodes,
                tags: &main,
            })
            .collect();
        ways.push(BoardingWayRef {
            id: 20,
            nodes: &stub_n,
            tags: &stub,
        });
        ways.push(BoardingWayRef {
            id: 30,
            nodes: &pier_n,
            tags: &pier,
        });
        ways.push(BoardingWayRef {
            id: 40,
            nodes: &ferry_n,
            tags: &ferry,
        });

        let promoted = promote_ferry_boarding_way_ids_with_min_road(
            &ways,
            &coords,
            FERRY_BOARDING_MAX_CHAIN_M,
            3,
        );
        assert!(
            promoted.is_empty(),
            "must not promote boarding that only reaches a disconnected stub"
        );

        // Same stub rejection with the default 50-junction gate and a 55-spoke hub.
        let big_road = tags(&[("highway", "primary")]);
        let mut big_coords = coords.clone();
        let hub = 1000i64;
        big_coords.insert(hub, (60.0, 5.0010));
        let mut spoke_node_lists: Vec<Vec<i64>> = Vec::new();
        for i in 0..55 {
            let leaf = 2000 + i;
            big_coords.insert(leaf, (60.0 + i as f64 * 0.00005, 5.0010));
            spoke_node_lists.push(vec![hub, leaf]);
        }
        let mut ways_big: Vec<BoardingWayRef<'_>> = spoke_node_lists
            .iter()
            .enumerate()
            .map(|(i, nodes)| BoardingWayRef {
                id: 100 + i as i64,
                nodes,
                tags: &big_road,
            })
            .collect();
        ways_big.push(BoardingWayRef {
            id: 20,
            nodes: &stub_n,
            tags: &stub,
        });
        ways_big.push(BoardingWayRef {
            id: 30,
            nodes: &pier_n,
            tags: &pier,
        });
        ways_big.push(BoardingWayRef {
            id: 40,
            nodes: &ferry_n,
            tags: &ferry,
        });
        let promoted_default =
            promote_ferry_boarding_way_ids(&ways_big, &big_coords, FERRY_BOARDING_MAX_CHAIN_M);
        assert!(
            promoted_default.is_empty(),
            "default 50-node gate must reject stub-only boarding"
        );

        // Sanity: short pier from berth 22 onto the hub must promote.
        let pier_to_main = [hub, 22];
        let ferry2 = [22i64, 23];
        let mut ways_ok: Vec<BoardingWayRef<'_>> = spoke_node_lists
            .iter()
            .enumerate()
            .map(|(i, nodes)| BoardingWayRef {
                id: 100 + i as i64,
                nodes,
                tags: &big_road,
            })
            .collect();
        ways_ok.push(BoardingWayRef {
            id: 30,
            nodes: &pier_to_main,
            tags: &pier,
        });
        ways_ok.push(BoardingWayRef {
            id: 40,
            nodes: &ferry2,
            tags: &ferry,
        });
        let promoted_ok =
            promote_ferry_boarding_way_ids(&ways_ok, &big_coords, FERRY_BOARDING_MAX_CHAIN_M);
        assert!(
            promoted_ok.contains(&30),
            "boarding to the substantial network must still promote"
        );
    }
}
