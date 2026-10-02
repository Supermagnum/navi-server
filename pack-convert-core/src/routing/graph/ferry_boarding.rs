//! Bounded car ferry terminal boarding links.
//!
//! OSM often connects `route=ferry` endpoints to the road network only via
//! `man_made=pier` / `highway=footway|platform` chains. Pass 1 drops those for
//! the car profile, leaving ferry landings as directed-graph islands.
//!
//! When enabled, we retain those candidate ways and promote only the short
//! undirected chains that connect a **car-capable** ferry endpoint to the
//! first car-drivable road node (length-bounded).

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

/// Returns the set of boarding-candidate way ids that lie on a bounded path
/// from a car-ferry endpoint node to a car-drivable road node.
pub fn promote_ferry_boarding_way_ids(
    ways: &[BoardingWayRef<'_>],
    coords: &HashMap<i64, (f64, f64)>,
    max_chain_m: f64,
) -> HashSet<i64> {
    let mut candidate_ids: HashSet<i64> = HashSet::new();
    let mut ferry_endpoint_nodes: HashSet<i64> = HashSet::new();
    let mut road_nodes: HashSet<i64> = HashSet::new();

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
            for &n in w.nodes {
                road_nodes.insert(n);
            }
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

    if ferry_endpoint_nodes.is_empty() || candidate_ids.is_empty() {
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
}
