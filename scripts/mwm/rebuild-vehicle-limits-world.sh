#!/usr/bin/env bash
# Rebuild 261105 world sidecars with vehicle_limits → SCRATCH publish only.
# Prefer NAVI_MWM_VEHICLE_LIMITS_PBF_DIR with per-country Geofabrik extracts
# (streamed; delete each after use if you fetch them in a wrapper).
# Falls back to NAVI_MWM_PLANET_PBF when set.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${NAVI_SERVER_REPO:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
cd "$REPO"

export NAVI_MWM_ENABLED=1
export NAVI_MWM_LEAFSET="${NAVI_MWM_LEAFSET:-$REPO/config/mwm/leafsets/planet.txt}"
export NAVI_MWM_COMAPS_ROOT="${NAVI_MWM_COMAPS_ROOT:-/media/navi/haaken-scratch/comaps-feasibility/comaps}"
export NAVI_MWM_BUILD_DIR="${NAVI_MWM_BUILD_DIR:-/media/navi/haaken-scratch/comaps-feasibility/omim-build-release}"
export NAVI_MWM_MAPS_BUILD="${NAVI_MWM_MAPS_BUILD:-/media/navi/haaken-scratch/mwm-maps-build}"
export NAVI_MWM_PUBLISHED_ROOT="${NAVI_MWM_PUBLISHED_ROOT:-/media/navi/haaken-scratch/mwm-published-vehicle-limits}"
export NAVI_MWM_COMAPS_VERSION="${NAVI_MWM_COMAPS_VERSION:-261105}"
export NAVI_MWM_PYTHON="${NAVI_MWM_PYTHON:-python3}"

if [[ -n "${NAVI_MWM_VEHICLE_LIMITS_PBF_DIR:-}" ]]; then
  echo "Using per-country PBF dir: $NAVI_MWM_VEHICLE_LIMITS_PBF_DIR"
elif [[ -n "${NAVI_MWM_VEHICLE_LIMITS_PBF:-}" ]]; then
  echo "Using single PBF: $NAVI_MWM_VEHICLE_LIMITS_PBF"
elif [[ -n "${NAVI_MWM_PLANET_PBF:-}" ]]; then
  export NAVI_MWM_VEHICLE_LIMITS_PBF="$NAVI_MWM_PLANET_PBF"
  echo "Using planet PBF: $NAVI_MWM_VEHICLE_LIMITS_PBF"
else
  echo "Set NAVI_MWM_VEHICLE_LIMITS_PBF_DIR (Geofabrik country extracts) or NAVI_MWM_PLANET_PBF" >&2
  exit 1
fi

"$REPO/scripts/mwm/run-comaps-release-bake.sh" --force \
  --publish-root "$NAVI_MWM_PUBLISHED_ROOT"

echo "World scratch OK at $NAVI_MWM_PUBLISHED_ROOT/${NAVI_MWM_COMAPS_VERSION}"
echo "Promote live only with scripts/mwm/publish-vehicle-limits-live.sh"
