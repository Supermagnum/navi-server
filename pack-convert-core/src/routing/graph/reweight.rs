//! Eco reweight without live ECU refinement (convert-only extract).

use rayon::prelude::*;

use crate::config::EcoConfig;
use crate::routing::elevation::ElevationService;

use super::builder::RouteGraph;

pub fn reweight_graph_for_eco(
    graph: &mut RouteGraph,
    elevation: &ElevationService,
    eco: &EcoConfig,
) {
    graph.edges.par_iter_mut().for_each(|edge| {
        let h_start = elevation.get_elevation(edge.start_lat, edge.start_lon);
        let h_end = elevation.get_elevation(edge.end_lat, edge.end_lon);
        let delta_h = match (h_start, h_end) {
            (Some(a), Some(b)) => b - a,
            _ => {
                edge.eco_weight = Some(eco.flat_energy_joules(edge.length_m).max(0.0));
                return;
            }
        };
        edge.eco_weight = Some(eco.segment_energy_joules(edge.length_m, delta_h).max(0.0));
    });
}
