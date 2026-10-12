#!/usr/bin/env bash
# Thin wrapper: gate on NAVI_REPEATERBOOK_ENABLED before importing the mirror.
# Installed as navi-repeaterbook.service when setup --apply-repeaterbook succeeds.
#
# Usage:
#   ./repeaterbook-mirror.sh
#   ./repeaterbook-mirror.sh --dry-run-fixtures plugins/repeaterbook/tests/fixtures

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAVI_SERVER_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

# HARD GATE — first behavioural check, before secrets or Python import side effects.
# Dry-run fixtures bypass this in the Python entrypoint when NAVI_REPEATERBOOK_DRY_RUN=1.
if [[ "${1:-}" != "--dry-run-fixtures" ]]; then
  enabled="${NAVI_REPEATERBOOK_ENABLED:-0}"
  case "${enabled}" in
    1|true|TRUE|yes|YES|on|ON) ;;
    *)
      log_info "repeaterbook disabled (NAVI_REPEATERBOOK_ENABLED=${enabled}); inert exit"
      exit 0
      ;;
  esac
  perm="${REPEATERBOOK_PERMISSION_CONFIRMED:-0}"
  case "${perm}" in
    1|true|TRUE|yes|YES|on|ON) ;;
    *)
      log_info "repeaterbook: REPEATERBOOK_PERMISSION_CONFIRMED!=1 — no fetch/publish"
      exit 0
      ;;
  esac
fi

export NAVI_PACK_ROOT="${NAVI_PACK_ROOT}"
export NAVI_REPEATERBOOK_ENABLED
export REPEATERBOOK_PERMISSION_CONFIRMED
export PYTHONPATH="${NAVI_SERVER_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

exec python3 -m plugins.repeaterbook "$@"
