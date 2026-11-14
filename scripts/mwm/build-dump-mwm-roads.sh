#!/usr/bin/env bash
# Install dump_mwm_roads into the CoMaps tools tree and build it.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

SRC_DIR="${SCRIPT_DIR}/tools/dump_mwm_roads"
DST_DIR="${NAVI_MWM_COMAPS_ROOT}/tools/navi_dump_mwm_roads"
TOOLS_CMAKE="${NAVI_MWM_COMAPS_ROOT}/tools/CMakeLists.txt"
BIN="${NAVI_MWM_BUILD_DIR}/dump_mwm_roads"

if [[ -x "$BIN" && "${NAVI_MWM_FORCE_REBUILD:-0}" != "1" ]]; then
  mwm_log "dump_mwm_roads already built: ${BIN}"
  echo "$BIN"
  exit 0
fi

[[ -d "${NAVI_MWM_COMAPS_ROOT}" ]] || mwm_die "NAVI_MWM_COMAPS_ROOT missing"
[[ -d "${NAVI_MWM_BUILD_DIR}" ]] || mwm_die "NAVI_MWM_BUILD_DIR missing (build generator first)"
[[ -f "$TOOLS_CMAKE" ]] || mwm_die "missing ${TOOLS_CMAKE}"

mkdir -p "$DST_DIR"
cp -a "${SRC_DIR}/dump_mwm_roads.cpp" "${SRC_DIR}/CMakeLists.txt" "$DST_DIR/"

if ! grep -q 'navi_dump_mwm_roads' "$TOOLS_CMAKE"; then
  printf '\nomim_add_tool_subdirectory(navi_dump_mwm_roads)\n' >>"$TOOLS_CMAKE"
  mwm_log "appended navi_dump_mwm_roads to tools/CMakeLists.txt"
fi

# Reconfigure if the target is unknown to the existing build tree.
if ! cmake --build "${NAVI_MWM_BUILD_DIR}" --target help 2>/dev/null | grep -q '^dump_mwm_roads'; then
  cmake -S "${NAVI_MWM_COMAPS_ROOT}" -B "${NAVI_MWM_BUILD_DIR}"
fi

cmake --build "${NAVI_MWM_BUILD_DIR}" --target dump_mwm_roads -j"${NAVI_MWM_BUILD_JOBS:-$(nproc)}"
[[ -x "$BIN" ]] || mwm_die "build did not produce ${BIN}"
mwm_log "built ${BIN}"
echo "$BIN"
