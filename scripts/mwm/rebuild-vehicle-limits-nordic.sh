#!/usr/bin/env bash
# Rebuild 261105 Nordic sidecars with vehicle_limits → SCRATCH publish only.
# Requires /media/navi (CoMaps root, maps-build, scratch publish). No sudo. No live.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${NAVI_SERVER_REPO:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
cd "$REPO"

export NAVI_MWM_ENABLED=1
export NAVI_MWM_LEAFSET="${NAVI_MWM_LEAFSET:-$REPO/config/mwm/leafsets/nordic.txt}"
export NAVI_MWM_COMAPS_ROOT="${NAVI_MWM_COMAPS_ROOT:-/media/navi/haaken-scratch/comaps-feasibility/comaps}"
export NAVI_MWM_BUILD_DIR="${NAVI_MWM_BUILD_DIR:-/media/navi/haaken-scratch/comaps-feasibility/omim-build-release}"
export NAVI_MWM_MAPS_BUILD="${NAVI_MWM_MAPS_BUILD:-/media/navi/haaken-scratch/mwm-maps-build}"
export NAVI_MWM_PUBLISHED_ROOT="${NAVI_MWM_PUBLISHED_ROOT:-/media/navi/haaken-scratch/mwm-published-vehicle-limits}"
# Prefer a Scandinavia extract for Nordic; Norway-only leaves empty SE/DK/FI sections.
export NAVI_MWM_VEHICLE_LIMITS_PBF="${NAVI_MWM_VEHICLE_LIMITS_PBF:-/media/navi/haaken-scratch/comaps-feasibility/inputs/scandinavia-norse.osm.pbf}"
if [[ ! -f "$NAVI_MWM_VEHICLE_LIMITS_PBF" ]]; then
  export NAVI_MWM_VEHICLE_LIMITS_PBF="${HOME}/.config/JOSM/nvdb_incline/norway-latest.osm.pbf"
  echo "WARN: scandinavia extract missing; falling back to Norway-only PBF: $NAVI_MWM_VEHICLE_LIMITS_PBF"
fi
export NAVI_MWM_PLANET_PBF="${NAVI_MWM_PLANET_PBF:-$NAVI_MWM_VEHICLE_LIMITS_PBF}"
export NAVI_MWM_COMAPS_VERSION="${NAVI_MWM_COMAPS_VERSION:-261105}"
export NAVI_MWM_PYTHON="${NAVI_MWM_PYTHON:-python3}"

echo "REPO=$REPO"
echo "LEAFSET=$NAVI_MWM_LEAFSET"
echo "VEHICLE_LIMITS_PBF=$NAVI_MWM_VEHICLE_LIMITS_PBF"
echo "SCRATCH_PUBLISH=$NAVI_MWM_PUBLISHED_ROOT"

"$REPO/scripts/mwm/run-comaps-release-bake.sh" --force \
  --publish-root "$NAVI_MWM_PUBLISHED_ROOT"

REPORT_DIR="$(find "$NAVI_MWM_MAPS_BUILD" -type d -path '*/meta/vehicle_limits' 2>/dev/null | head -1 || true)"
export REPORT_DIR
if [[ -n "${REPORT_DIR:-}" && -d "$REPORT_DIR" ]]; then
  python3 - <<'PY'
import json, os
from pathlib import Path
d = Path(os.environ["REPORT_DIR"])
tagged = matched = unmatched = records = 0
files = sorted(d.glob("*.report.json"))
for p in files:
    r = json.loads(p.read_text())
    tagged += int(r.get("tagged_ways") or 0)
    matched += int(r.get("matched_ways") or 0)
    unmatched += int(r.get("unmatched_ways") or 0)
    records += int(r.get("records") or 0)
print(json.dumps({
    "report_dir": str(d),
    "leaves": len(files),
    "tagged_ways_sum": tagged,
    "matched_ways_sum": matched,
    "unmatched_ways_sum": unmatched,
    "records_sum": records,
    "match_rate": matched / max(1, matched + unmatched),
}, indent=2))
PY
  echo "Per-leaf reports: $REPORT_DIR"
fi

SIDECAR_DIR="$NAVI_MWM_PUBLISHED_ROOT/${NAVI_MWM_COMAPS_VERSION}"
if [[ -x "$REPO/target/release/navi-mwm-sidecar" ]]; then
  BIN="$REPO/target/release/navi-mwm-sidecar"
else
  cargo build -p navi-mwm-sidecar --release
  BIN="$REPO/target/release/navi-mwm-sidecar"
fi
for leaf in Norway_Hedmark Norway_Oppland Norway_Southern; do
  sc="$SIDECAR_DIR/${leaf}.navi-sidecar.v1.zst"
  [[ -f "$sc" ]] || continue
  echo "=== inspect $leaf ==="
  "$BIN" inspect --sidecar "$sc"
done

echo "Nordic scratch OK. Promote live only with scripts/mwm/publish-vehicle-limits-live.sh"
