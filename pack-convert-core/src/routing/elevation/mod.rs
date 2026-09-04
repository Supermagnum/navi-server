//! Local on-disk DEM sampling (no network downloader).

mod cache;
mod reader;
mod service;
pub mod tile_id;

pub use crate::download::DownloadControl;
pub use cache::ElevationCache;
pub use reader::ElevationReader;
pub use service::ElevationService;
pub use tile_id::{bbox_to_tiles, HgtTileId};
