#!/usr/bin/env bash
# Run RepeaterBook then RadioID mirrors. Failure isolation: one source failing
# does not skip or roll back the other. Exit 1 if either failed (non-inert).
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAVI_SERVER_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

rc_rb=0
rc_rid=0
set +e
"${SCRIPT_DIR}/repeaterbook-mirror.sh"
rc_rb=$?
"${SCRIPT_DIR}/radioid-mirror.sh"
rc_rid=$?
set -e

log_info "repeater-mirror-all: repeaterbook_rc=${rc_rb} radioid_rc=${rc_rid}"
if [[ "$rc_rb" -ne 0 || "$rc_rid" -ne 0 ]]; then
  exit 1
fi
exit 0
