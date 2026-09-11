//! Road network graph construction for pack convert.

mod bbox_build;
mod bike_suitability;
mod builder;
mod reweight;
mod surface_quality;

pub use bbox_build::TiledBuildTimings;
pub use bike_suitability::{
    apply_bike_suitability, apply_bike_suitability_from_pbf, load_way_terrain_tags,
    tags_unsuitable_for, way_id_from_edge_id, BikeCapability,
};
pub use builder::{
    append_seasonal_closure_report, edge_is_motorway_grade, format_route_avoidance_report,
    highway_is_motorway, max_waypoint_snap_m, profile_locks_avoid_motorways, GraphEdge, RouteGraph,
    RouteOptions, RoutingProfile, SnapTooFar, WetlandApplyStats,
};
pub use reweight::reweight_graph_for_eco;
pub use surface_quality::{
    apply_surface_preference, apply_surface_quality_from_pbf, best_incident_surface,
    classify_surface_tags, edge_has_posted_maxspeed, edge_maxspeed_multiplier,
    edge_motor_soft_multiplier, edge_surface_multiplier, infer_surface_from_highway,
    surface_transition_cost_m, worst_incident_surface, MotorSoftCostProfile, SurfaceQuality,
    SurfaceRoutingMode, MAXSPEED_MISSING_CAR, MAXSPEED_MISSING_TRUCK,
    SNAP_VIRTUAL_APPROACH_SURFACE, SURFACE_MARGINAL_EDGE_PENALTY, SURFACE_MARGINAL_MOBILE_HOME,
    SURFACE_MARGINAL_MOTORCYCLE, SURFACE_MARGINAL_TRUCK, SURFACE_POOR_EDGE_PENALTY,
    SURFACE_POOR_MOBILE_HOME, SURFACE_POOR_MOTORCYCLE, SURFACE_POOR_TRUCK,
    SURFACE_TRANSITION_MAX_CLASS_DROP, SURFACE_TRANSITION_PENALTY_M,
};
