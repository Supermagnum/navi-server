#!/usr/bin/env bash
# Thin wrapper for RadioID mirror. Gates before Python import side effects.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAVI_SERVER_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

if [[ "${1:-}" != "--dry-run-fixtures" ]]; then
  enabled="${NAVI_RADIOID_ENABLED:-0}"
  case "${enabled}" in
    1|true|TRUE|yes|YES|on|ON) ;;
    *)
      log_info "radioid disabled (NAVI_RADIOID_ENABLED=${enabled}); inert exit"
      exit 0
      ;;
  esac
  perm="${RADIOID_PERMISSION_CONFIRMED:-0}"
  case "${perm}" in
    1|true|TRUE|yes|YES|on|ON) ;;
    *)
      log_info "radioid: RADIOID_PERMISSION_CONFIRMED!=1 — no fetch/publish"
      exit 0
      ;;
  esac
fi

export NAVI_PACK_ROOT="${NAVI_PACK_ROOT}"
export NAVI_RADIOID_ENABLED
export RADIOID_PERMISSION_CONFIRMED
export PYTHONPATH="${NAVI_SERVER_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

exec python3 -m plugins.radioid "$@"
