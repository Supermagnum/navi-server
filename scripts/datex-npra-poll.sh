#!/usr/bin/env bash
# Thin wrapper: gate on NAVI_DATEX_NPRA_ENABLED before importing the Python poller.
# Installed as navi-datex-npra.service only when setup --apply-datex succeeds.
#
# Usage:
#   ./datex-npra-poll.sh
#   ./datex-npra-poll.sh --no-jitter

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAVI_SERVER_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

# HARD GATE — first behavioural check, before secrets or Python import side effects.
enabled="${NAVI_DATEX_NPRA_ENABLED:-0}"
case "${enabled}" in
  1|true|TRUE|yes|YES|on|ON) ;;
  *)
    log_info "datex_npra disabled (NAVI_DATEX_NPRA_ENABLED=${enabled}); inert exit"
    exit 0
    ;;
esac

export NAVI_PACK_ROOT="${NAVI_PACK_ROOT}"
export NAVI_DATEX_NPRA_ENABLED
export PYTHONPATH="${NAVI_SERVER_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

exec python3 -m plugins.datex_npra.poll "$@"
