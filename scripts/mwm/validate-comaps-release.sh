#!/usr/bin/env bash
# Validate a comaps-release sidecar staging dir before publish.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

VERSION_DIR=""
WORK_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version-dir) VERSION_DIR="$2"; shift 2 ;;
    --work-dir) WORK_DIR="$2"; shift 2 ;;
    *) mwm_die "unknown arg: $1" ;;
  esac
done
[[ -n "$VERSION_DIR" && -d "$VERSION_DIR" ]] || mwm_die "need --version-dir"

ROOT="$(mwm_repo_root)"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-${ROOT}/target}"
SIDECAR_BIN="${CARGO_TARGET_DIR}/release/navi-mwm-sidecar"
[[ -x "$SIDECAR_BIN" ]] || mwm_die "navi-mwm-sidecar missing"

python3 - "$VERSION_DIR" "$NAVI_MWM_LEAFSET" "$SIDECAR_BIN" <<'PY'
import json, subprocess, sys
from pathlib import Path

version = Path(sys.argv[1])
leafset = Path(sys.argv[2])
sidecar_bin = sys.argv[3]
leaves = [ln.strip() for ln in leafset.read_text().splitlines() if ln.strip() and not ln.strip().startswith("#")]
errors = []
proc = json.loads((version / "processed.json").read_text()) if (version / "processed.json").is_file() else {"leaves": {}}
processed = proc.get("leaves") or {}

attr = version / "attribution.json"
if not attr.is_file():
    errors.append("missing attribution.json")
else:
    data = json.loads(attr.read_text())
    blob = json.dumps(data).lower()
    if "mapterhorn" not in blob:
        errors.append("attribution.json must name Mapterhorn")
    if "openstreetmap" not in blob and "osm" not in blob:
        errors.append("attribution.json must credit OpenStreetMap")
    if "comaps" not in blob:
        errors.append("attribution.json must credit CoMaps")

for leaf in leaves:
    sc = version / f"{leaf}.navi-sidecar.v1.zst"
    if not sc.is_file():
        # Partial runs are allowed; only validate present sidecars.
        continue
    meta = processed.get(leaf)
    if not meta:
        errors.append(f"{leaf}: missing processed fingerprint record")
        continue
    p = subprocess.run(
        [sidecar_bin, "inspect", "--sidecar", str(sc)],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        errors.append(f"{leaf}: inspect failed: {p.stderr.strip()}")
        continue
    info = json.loads(p.stdout)
    if info.get("mwm_sha256") != meta.get("mwm_sha256"):
        errors.append(f"{leaf}: sidecar mwm_sha256 != processed record")
    if int(info.get("mwm_size") or 0) != int(meta.get("mwm_bytes") or -1):
        errors.append(f"{leaf}: sidecar mwm_size != processed record")
    if "elevation_overrides" not in (info.get("sections") or []):
        errors.append(f"{leaf}: missing elevation_overrides section")

if errors:
    for e in errors:
        print("ERROR:", e)
    raise SystemExit(1)
print("validate-comaps-release OK")
PY

# Six-route check before publish: stage configurable leaf MWMs + sectioned sidecars.
# Set NAVI_MWM_SKIP_ROUTE_CHECK=1 to skip (streaming CI without disk for MWMs).
# Leaf list: NAVI_MWM_ROUTE_CHECK_LEAVES (default config/mwm/leafsets/six-route.txt).
if [[ "${NAVI_MWM_SKIP_ROUTE_CHECK:-0}" == "1" ]]; then
  mwm_log "skip six-route check (NAVI_MWM_SKIP_ROUTE_CHECK=1)"
elif [[ -n "$WORK_DIR" && -x "${NAVI_MWM_BUILD_DIR}/routes_builder_tool" ]]; then
  ROOT="$(mwm_repo_root)"
  LEAFSET="${NAVI_MWM_ROUTE_CHECK_LEAVES:-${ROOT}/config/mwm/leafsets/six-route.txt}"
  STAGE="${WORK_DIR}/route-mwms"
  mwm_log "six-route check leafset=${LEAFSET} stage=${STAGE}"
  "${SCRIPT_DIR}/run-six-route-check.sh" \
    --stage-dir "$STAGE" \
    --sidecar-dir "$VERSION_DIR" \
    --leafset "$LEAFSET"
else
  mwm_log "six-route check skipped (need --work-dir and routes_builder_tool)"
fi

mwm_log "validate-comaps-release passed"
