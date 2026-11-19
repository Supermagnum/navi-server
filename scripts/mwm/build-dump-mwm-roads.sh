#!/usr/bin/env bash
# Install dump_mwm_roads into NAVI_MWM_BUILD_DIR.
#
# Preferred production path: copy a known-good prebuilt binary
# (NAVI_MWM_DUMP_SRC) into BUILD_DIR — no CoMaps cmake tree required.
# Fallback: patch tools into NAVI_MWM_COMAPS_ROOT and cmake --build.
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

mkdir -p "${NAVI_MWM_BUILD_DIR}"

# Optional: install a prebuilt binary (live host / operator apply).
if [[ -n "${NAVI_MWM_DUMP_SRC:-}" ]]; then
  [[ -x "${NAVI_MWM_DUMP_SRC}" ]] || mwm_die "NAVI_MWM_DUMP_SRC not executable: ${NAVI_MWM_DUMP_SRC}"
  cp -a "${NAVI_MWM_DUMP_SRC}" "$BIN"
  chmod a+rx "$BIN"
  mwm_log "installed dump_mwm_roads from ${NAVI_MWM_DUMP_SRC} -> ${BIN}"
  echo "$BIN"
  exit 0
fi

[[ -d "${NAVI_MWM_COMAPS_ROOT}" ]] || mwm_die "NAVI_MWM_COMAPS_ROOT missing (${NAVI_MWM_COMAPS_ROOT}); install dump via NAVI_MWM_DUMP_SRC=…/dump_mwm_roads or clone CoMaps"
[[ -d "${NAVI_MWM_BUILD_DIR}" ]] || mwm_die "NAVI_MWM_BUILD_DIR missing (build generator first)"
[[ -f "$TOOLS_CMAKE" ]] || mwm_die "missing ${TOOLS_CMAKE} (full CoMaps checkout required to build; or set NAVI_MWM_DUMP_SRC)"

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
