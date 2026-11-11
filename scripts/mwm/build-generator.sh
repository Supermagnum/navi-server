#!/usr/bin/env bash
# Fetch pinned CoMaps and build generator_tool + routes_builder_tool only.
# Output tree lives OUTSIDE the navi-server repo (NAVI_MWM_COMAPS_ROOT / BUILD_DIR).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

TAG="$(mwm_pin_field tag)"
SHA="$(mwm_pin_field sha)"
REPO="$(python3 -c "import json; print(json.load(open('$NAVI_MWM_PIN_JSON')).get('repo','https://codeberg.org/comaps/comaps.git'))")"

mwm_log "build-generator tag=${TAG} sha=${SHA}"
mwm_log "comaps_root=${NAVI_MWM_COMAPS_ROOT} build_dir=${NAVI_MWM_BUILD_DIR}"

mkdir -p "$(dirname "$NAVI_MWM_COMAPS_ROOT")" "$(dirname "$NAVI_MWM_BUILD_DIR")"

if [[ -d "${NAVI_MWM_COMAPS_ROOT}/.git" ]]; then
  git -C "$NAVI_MWM_COMAPS_ROOT" fetch --tags origin 2>/dev/null || \
    git -C "$NAVI_MWM_COMAPS_ROOT" fetch --tags
  git -C "$NAVI_MWM_COMAPS_ROOT" checkout --force "$SHA"
else
  git clone "$REPO" "$NAVI_MWM_COMAPS_ROOT"
  git -C "$NAVI_MWM_COMAPS_ROOT" checkout --force "$SHA"
fi

HAVE_SHA="$(git -C "$NAVI_MWM_COMAPS_ROOT" rev-parse HEAD)"
[[ "$HAVE_SHA" == "$SHA" ]] || mwm_die "comaps HEAD ${HAVE_SHA} != pinned ${SHA}"

GEN="${NAVI_MWM_BUILD_DIR}/generator_tool"
RB="${NAVI_MWM_BUILD_DIR}/routes_builder_tool"
if [[ -x "$GEN" && -x "$RB" ]]; then
  # Rebuild if binary older than a forced flag
  if [[ "${NAVI_MWM_FORCE_REBUILD:-0}" != "1" ]]; then
    mwm_log "generator binaries already present; skip cmake (NAVI_MWM_FORCE_REBUILD=1 to rebuild)"
    exit 0
  fi
fi

mkdir -p "$NAVI_MWM_BUILD_DIR"
cd "$NAVI_MWM_BUILD_DIR"

# Prefer Ninja when available. CONFIG only generator-side targets.
CMAKE_ARGS=(
  -DCMAKE_BUILD_TYPE=Release
  -DWITH_TESTS=OFF
)
if command -v ninja >/dev/null 2>&1; then
  CMAKE_ARGS+=(-G Ninja)
fi

# Reuse existing cache if present
if [[ ! -f CMakeCache.txt ]]; then
  cmake "${CMAKE_ARGS[@]}" "$NAVI_MWM_COMAPS_ROOT"
fi

# Build only the required targets (and whatever they need transitively).
cmake --build . --target generator_tool routes_builder_tool -j"${NAVI_MWM_BUILD_JOBS:-$(nproc)}"

[[ -x "$GEN" ]] || mwm_die "missing $GEN"
[[ -x "$RB" ]] || mwm_die "missing $RB"
mwm_log "build-generator OK"
