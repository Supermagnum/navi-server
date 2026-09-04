//! Named defaults and safety/eco parameters used by pack convert.

mod defaults;
mod eco;
mod safety;

pub use defaults::*;
pub use eco::{motorcycle_eco_config, EcoConfig};
pub use safety::SafetyConfig;

use serde::{Deserialize, Serialize};

/// Travel / routing profile (subset of Navi app profiles).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum Profile {
    #[default]
    Car,
    CarElectric,
    Truck,
    TruckElectric,
    MobileHome,
    Hiking,
    Cycling,
    CyclingElectric,
    Motorcycle,
    MotorcycleElectric,
}

/// Physical vehicle limits used to filter OSM tagged restrictions.
#[derive(Debug, Clone, Serialize, Deserialize, Default, PartialEq)]
pub struct VehicleLimits {
    pub axle_weight_kg: Option<f64>,
    #[serde(default)]
    pub bogie_weight_kg: Option<f64>,
    pub height_m: Option<f64>,
    pub width_m: Option<f64>,
    #[serde(default)]
    pub length_m: Option<f64>,
    pub total_weight_kg: Option<f64>,
}
