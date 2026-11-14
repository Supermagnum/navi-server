#!/usr/bin/env bash
# Daily CoMaps release check. With NAVI_MWM_ENABLED=0: detect-only (log + exit).
# Never starts a bake or publish unless NAVI_MWM_ENABLED=1 and check exits 10.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

set +e
"${SCRIPT_DIR}/check-comaps-release.sh"
ec=$?
set -e

if [[ "$ec" -eq 0 ]]; then
  mwm_log "check: published CoMaps release is current (no bake)"
  exit 0
fi

if [[ "$ec" -ne 10 ]]; then
  mwm_log "check failed exit=${ec}"
  exit "$ec"
fi

# Exit 10 = new / missing published release.
if [[ "${NAVI_MWM_ENABLED}" != "1" ]]; then
  mwm_log "check: NEW CoMaps release detected (exit 10); NAVI_MWM_ENABLED=0 — detect-only, not starting bake"
  exit 0
fi

mwm_log "check: NEW CoMaps release detected; NAVI_MWM_ENABLED=1 — starting bake"
exec "${SCRIPT_DIR}/run-mwm-bake.sh"
