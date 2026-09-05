#!/usr/bin/env bash
# Launch (or resume) the batched planet-leaf bake in a detached screen session
# with a wrapper that always logs END and never lets `set -e` swallow the bake
# exit before cleanup. Prefer this over ad-hoc `screen -dmS … bash -lc 'set -e…'`.
#
# Usage:
#   ./start-planet-leaves-screen.sh
#   ./start-planet-leaves-screen.sh --resume
#
# Does NOT clear an existing PAUSED hold or kill a running session — refuse if
# navi-planet-leaves is already present.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

RESUME_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --resume) RESUME_ARGS+=(--resume); shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
done

export SCREENDIR="${SCREENDIR:-$HOME/.screen}"
mkdir -p "$SCREENDIR"

if screen -ls 2>/dev/null | grep -q '\.navi-planet-leaves[[:space:]]'; then
  die "screen session navi-planet-leaves already exists — reattach or kill it first"
fi

LOG="${NAVI_LOG_DIR}/planet-leaves/run.log"
mkdir -p "$(dirname "$LOG")"

# shellcheck disable=SC2016
screen -dmS navi-planet-leaves bash -lc '
  set -uo pipefail
  cd "'"${SCRIPT_DIR}"'"
  unset NAVI_CONVERT_DIR NAVI_REGIONS_CONF || true
  export NAVI_PACK_CONFIG="'"${NAVI_PACK_CONFIG:-${NAVI_PACK_ROOT}/config.env}"'"
  export NAVI_PLANET_BATCH_SCRATCH_GIB="'"${NAVI_PLANET_BATCH_SCRATCH_GIB:-80}"'"
  export SCREENDIR="'"${SCREENDIR}"'"
  LOG="'"${LOG}"'"
  exec > >(tee -a "$LOG") 2>&1
  echo "==== START utc=$(date -u +%Y%m%dT%H%M%SZ) launcher=start-planet-leaves-screen.sh ===="
  set +e
  ./run-planet-leaves-batched.sh '"${RESUME_ARGS[*]}"'
  rc=$?
  set -e
  echo "==== END utc=$(date -u +%Y%m%dT%H%M%SZ) rc=$rc ===="
  # pause_run holds forever (rc never returned). If we get here, exit cleanly.
  exit "$rc"
'

sleep 1
screen -ls
log_info "started screen navi-planet-leaves; reattach: SCREENDIR=${SCREENDIR} screen -r navi-planet-leaves"
