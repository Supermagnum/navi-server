#!/usr/bin/env bash
# Thin wrapper for OSM amateur-radio repeater extract. Gates before Python.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAVI_SERVER_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

if [[ "${1:-}" != "--dry-run-fixtures" ]]; then
  enabled="${NAVI_OSM_REPEATERS_ENABLED:-0}"
  case "${enabled}" in
    1|true|TRUE|yes|YES|on|ON) ;;
    *)
      log_info "osm_repeaters disabled (NAVI_OSM_REPEATERS_ENABLED=${enabled}); inert exit"
      exit 0
      ;;
  esac
fi

export NAVI_PACK_ROOT="${NAVI_PACK_ROOT}"
export NAVI_EXTRACTS_DIR="${NAVI_EXTRACTS_DIR}"
export NAVI_REGIONS_CONF="${NAVI_REGIONS_CONF}"
export NAVI_OSM_REPEATERS_ENABLED
export PYTHONPATH="${NAVI_SERVER_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

exec python3 -m plugins.osm_repeaters "$@"
