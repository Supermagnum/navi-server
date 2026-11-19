#!/usr/bin/env bash
# Install routes_builder_tool into NAVI_MWM_BUILD_DIR (same pattern as dump_mwm_roads).
#
# Production validate/route-check requires this binary under BUILD_DIR. Prefer a
# copy owned by the service user — do not point navit-server at haaken-scratch.
#
# Usage:
#   NAVI_MWM_ROUTES_BUILDER_SRC=/path/to/routes_builder_tool \
#     ./scripts/mwm/install-routes-builder-tool.sh
#
# Or let the script default SRC to the known scratch feasibility binary when
# present (operator machines only).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

DST="${NAVI_MWM_BUILD_DIR}/routes_builder_tool"
if [[ -x "$DST" && "${NAVI_MWM_FORCE_REBUILD:-0}" != "1" ]]; then
  mwm_log "routes_builder_tool already present: ${DST}"
  echo "$DST"
  exit 0
fi

DEFAULT_SRC="/media/navi/haaken-scratch/comaps-feasibility/omim-build-release/routes_builder_tool"
SRC="${NAVI_MWM_ROUTES_BUILDER_SRC:-}"
# Default SRC only when readable by this user (navit-server cannot traverse haaken-scratch).
if [[ -z "$SRC" && -x "$DEFAULT_SRC" ]]; then
  SRC="$DEFAULT_SRC"
fi
[[ -n "$SRC" && -x "$SRC" ]] || \
  mwm_die "need executable NAVI_MWM_ROUTES_BUILDER_SRC (or readable default ${DEFAULT_SRC}); on live use sudo bash /tmp/navi-mwm-install-routes-builder.sh"

mkdir -p "${NAVI_MWM_BUILD_DIR}"
install -m 0755 "$SRC" "$DST"
# Help must run (dynamic linker / Qt deps).
"$DST" --help >/dev/null
mwm_log "installed routes_builder_tool: ${SRC} -> ${DST}"
echo "$DST"
