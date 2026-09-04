//! Standalone pack conversion library extracted from Navi `driver-break-core`
//! for the navi-server bake pipeline. No HTTP / UniFFI / Android / SQLite.

pub mod config;
pub mod download;
pub mod poi;
pub mod routing;

pub use routing::graph::RoutingProfile;
pub use routing::indexed::{convert_region_packs, ConvertOptions, ConvertReport};
pub use routing::workers::WorkerPoolPlan;
