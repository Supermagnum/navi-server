#!/usr/bin/env bash
# Scratch Nordic e2e (not live). Invoked under MemoryMax=14G from /tmp/navi-mwm-apply.sh.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

export NAVI_MWM_ENABLED=0
export NAVI_MWM_COMAPS_ROOT="${NAVI_MWM_COMAPS_ROOT:-/media/navi/haaken-scratch/comaps-feasibility/comaps}"
export NAVI_MWM_BUILD_DIR="${NAVI_MWM_BUILD_DIR:-/media/navi/haaken-scratch/comaps-feasibility/omim-build-release}"
export NAVI_MWM_MAPS_BUILD="${NAVI_MWM_MAPS_BUILD:-/media/navi/haaken-scratch/mwm-maps-build}"
export NAVI_MWM_HGT_CACHE="${NAVI_MWM_HGT_CACHE:-${ROOT}/data/elevation/hgt-mapterhorn}"
export NAVI_MWM_PLANET_PBF="${NAVI_MWM_PLANET_PBF:-/media/navi/haaken-scratch/comaps-feasibility/inputs/scandinavia-norse.osm.pbf}"
# PMTiles optional; requires pmtiles+Pillow. Scratch e2e defaults to void hardlinks
# for missing cells while --keep-existing preserves any seeded real HGT.
export NAVI_MWM_MAPTERHORN_PMTILES="${NAVI_MWM_MAPTERHORN_PMTILES:-}"
export NAVI_MWM_HGT_VOID_FILL="${NAVI_MWM_HGT_VOID_FILL:-1}"
# Prefer spike2 venv if present (pmtiles/Pillow) when PMTiles is set.
if [[ -n "${NAVI_MWM_MAPTERHORN_PMTILES}" && -x /tmp/navi-mwm-spike2/venv/bin/python ]]; then
  export NAVI_MWM_HGT_PYTHON=/tmp/navi-mwm-spike2/venv/bin/python
fi
export NAVI_MWM_THREADS_FEATURES="${NAVI_MWM_THREADS_FEATURES:-4}"
export NAVI_MWM_NODE_STORAGE="${NAVI_MWM_NODE_STORAGE:-map}"
export NAVI_MWM_QT_BIN_DIR="${NAVI_MWM_QT_BIN_DIR:-/media/navi/haaken-scratch/comaps-feasibility/qt6-local/opt/usr/lib/qt6/bin}"
export NAVI_MWM_QT_LIB_DIR="${NAVI_MWM_QT_LIB_DIR:-/media/navi/haaken-scratch/comaps-feasibility/qt6-local/opt/usr/lib/x86_64-linux-gnu}"
export NAVI_MWM_LEAFSET="${NAVI_MWM_LEAFSET:-${ROOT}/config/mwm/leafsets/nordic.txt}"
export NAVI_MWM_PIN_JSON="${ROOT}/config/mwm/comaps-pin.json"
export NAVI_MWM_STATE_DIR="${ROOT}/data/state"
export NAVI_MWM_LOCK="${NAVI_MWM_STATE_DIR}/pack-pipeline.lock"
export NAVI_MWM_AMBIGUOUS_THRESHOLD="${NAVI_MWM_AMBIGUOUS_THRESHOLD:-1000000}"
PUBLISH_ROOT="${1:-/media/navi/haaken-scratch/mwm-published}"
E2E_LOG="${NAVI_MWM_MAPS_BUILD}/navi-logs/scratch-e2e-$(date -u +%Y%m%dT%H%M%SZ).log"
mkdir -p "$(dirname "$E2E_LOG")" "$PUBLISH_ROOT"

mwm_load_defaults
exec > >(tee -a "$E2E_LOG") 2>&1
mwm_log "scratch e2e start log=${E2E_LOG} publish_root=${PUBLISH_ROOT}"

mwm_with_lock bash -c "
  set -euo pipefail
  source '${SCRIPT_DIR}/lib/common.sh'
  mwm_load_defaults
  '${SCRIPT_DIR}/build-generator.sh'
  VERSION_DIR=\"\$('${SCRIPT_DIR}/run-generator.sh' --skip-lock --force | tee /dev/stderr | tail -1)\"
  echo VERSION_DIR=\$VERSION_DIR
  # Sidecars: annotate can be multi-hour on full Nordic PBF; allow empty via env.
  if [[ \"\${NAVI_MWM_SKIP_ANNOTATE:-0}\" == \"1\" ]]; then
    '${SCRIPT_DIR}/build-sidecars.sh' --version-dir \"\$VERSION_DIR\" --skip-annotate
  else
    '${SCRIPT_DIR}/build-sidecars.sh' --version-dir \"\$VERSION_DIR\" --pbf \"\$NAVI_MWM_PLANET_PBF\"
  fi
  '${SCRIPT_DIR}/validate-release.sh' --version-dir \"\$VERSION_DIR\"
  '${SCRIPT_DIR}/publish-mwm.sh' --version-dir \"\$VERSION_DIR\" --publish-root '${PUBLISH_ROOT}'
  echo DONE version=\$VERSION_DIR
"
