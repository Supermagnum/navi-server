#pragma once
// Navi wetland cost hook for CoMaps EdgeEstimator (scratch validation).
// Loads sectioned NVSC sidecars ({stem}.navi-sidecar.v1.zst) from
// NAVI_WETLAND_SIDECAR_DIR — the same format clients use.
// SoftAvoid ×5 (weight/ETA); HardAvoid → enormous penalty (avoid).

#include "routing/segment.hpp"

#include <cstdint>
#include <string>

namespace navi_spike
{

enum class WetlandClass : uint8_t
{
  None = 0,
  SoftAvoid = 1,
  HardAvoid = 2,
};

class WetlandSidecar
{
public:
  // Load sidecar next to MWM stem under NAVI_WETLAND_SIDECAR_DIR.
  // Returns false if missing/disabled; true if loaded (possibly empty).
  static bool EnsureLoadedForMwm(std::string const & mwmStem, std::string const & mwmPath);

  static WetlandClass Lookup(uint32_t featureId, uint32_t segmentIdx, bool forward);

  // Multiplier applied to travel time / weight seconds.
  static double CostMultiplier(WetlandClass c);

  static bool Enabled();
};

// Apply after base weight is computed: weight *= multiplier.
inline double ApplyWetlandCost(routing::Segment const & segment, double weightSeconds)
{
  if (!WetlandSidecar::Enabled())
    return weightSeconds;
  auto const cls = WetlandSidecar::Lookup(
      segment.GetFeatureId(), segment.GetSegmentIdx(), segment.IsForward());
  return weightSeconds * WetlandSidecar::CostMultiplier(cls);
}

}  // namespace navi_spike
