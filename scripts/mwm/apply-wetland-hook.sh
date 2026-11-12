#!/usr/bin/env bash
# Apply sectioned-NVSC wetland hook into the scratch CoMaps tree and rebuild
# routes_builder_tool. No sudo. Idempotent.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

COMAPS="${NAVI_MWM_COMAPS_ROOT}"
BUILD="${NAVI_MWM_BUILD_DIR}"
HPP_SRC="${SCRIPT_DIR}/hook/navi_wetland_cost.hpp"
CPP_SRC="${SCRIPT_DIR}/hook/navi_wetland_cost.cpp"
[[ -f "$HPP_SRC" && -f "$CPP_SRC" ]] || mwm_die "hook sources missing under scripts/mwm/hook/"
[[ -d "$COMAPS/libs/routing" ]] || mwm_die "CoMaps routing dir missing: $COMAPS"

cp -a "$HPP_SRC" "$COMAPS/libs/routing/navi_wetland_cost.hpp"
cp -a "$CPP_SRC" "$COMAPS/libs/routing/navi_wetland_cost.cpp"

CMAKE="$COMAPS/libs/routing/CMakeLists.txt"
if ! grep -q 'navi_wetland_cost.cpp' "$CMAKE"; then
  python3 - "$CMAKE" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
t = p.read_text()
needle = "  edge_estimator.cpp\n"
insert = needle + "  navi_wetland_cost.cpp\n  navi_wetland_cost.hpp\n"
if needle not in t:
    raise SystemExit(f"needle not found in {p}")
p.write_text(t.replace(needle, insert, 1))
print("CMakeLists.txt: added navi_wetland_cost sources")
PY
fi
if ! grep -q 'PRIVATE zstd' "$CMAKE"; then
  python3 - "$CMAKE" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
t = p.read_text()
needle = "target_link_libraries(${PROJECT_NAME}\n  PUBLIC\n    opening_hours\n)\n"
insert = (
    needle
    + "\n# navi wetland sidecar (zstd sectioned NVSC)\n"
    + "target_link_libraries(${PROJECT_NAME} PRIVATE zstd)\n"
)
if needle not in t:
    raise SystemExit("opening_hours link block not found")
p.write_text(t.replace(needle, insert, 1))
print("CMakeLists.txt: linked zstd for wetland hook")
PY
fi

EST="$COMAPS/libs/routing/edge_estimator.cpp"
if ! grep -q 'navi_wetland_cost.hpp' "$EST"; then
  python3 - "$EST" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
t = p.read_text()
if '#include "routing/navi_wetland_cost.hpp"' in t:
    raise SystemExit(0)
old = '#include "routing/edge_estimator.hpp"\n'
new = old + '#include "routing/navi_wetland_cost.hpp"\n'
if old not in t:
    raise SystemExit("edge_estimator.hpp include not found")
t = t.replace(old, new, 1)
# Soft hook call sites are applied by spike2 apply script if present; keep
# multiplicative path when already patched.
p.write_text(t)
print("edge_estimator.cpp: include added")
PY
fi

# Ensure EdgeEstimator multiplies by wetland cost when env is set (idempotent).
if ! grep -q 'navi_spike::WetlandSidecar' "$EST"; then
  mwm_log "WARNING: edge_estimator.cpp has no navi_spike call sites yet;"
  mwm_log "  applying spike2 patch helper if available"
  SPIKE_APPLY=/tmp/navi-mwm-spike2/scripts/apply_wetland_hook.sh
  if [[ -x "$SPIKE_APPLY" ]]; then
    # Spike apply copies its own (flat) sources; re-copy sectioned sources after.
    bash "$SPIKE_APPLY" || true
    cp -a "$HPP_SRC" "$COMAPS/libs/routing/navi_wetland_cost.hpp"
    cp -a "$CPP_SRC" "$COMAPS/libs/routing/navi_wetland_cost.cpp"
  else
    mwm_die "edge_estimator not hooked and spike2 apply script missing"
  fi
fi

# Always overwrite with sectioned loader after any spike apply.
cp -a "$HPP_SRC" "$COMAPS/libs/routing/navi_wetland_cost.hpp"
cp -a "$CPP_SRC" "$COMAPS/libs/routing/navi_wetland_cost.cpp"

mkdir -p "$BUILD"
# Scratch CoMaps trees often lack system freetype headers; use bundled/qt paths.
FT_INCLUDES=(
  "${COMAPS}/../qt6-local/opt/usr/include/freetype2"
  "${COMAPS}/3party/freetype/freetype/include"
)
for d in "${FT_INCLUDES[@]}"; do
  if [[ -d "$d" ]]; then
    export CPATH="${d}:${CPATH:-}"
    export CPLUS_INCLUDE_PATH="${d}:${CPLUS_INCLUDE_PATH:-}"
  fi
done
cmake --build "$BUILD" --target routes_builder_tool -j"$(nproc)"
[[ -x "$BUILD/routes_builder_tool" ]] || mwm_die "routes_builder_tool missing after build"
mwm_log "wetland hook (sectioned NVSC) applied; routes_builder_tool rebuilt"
