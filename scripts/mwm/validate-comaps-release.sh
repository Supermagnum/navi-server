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

# Optional route check: re-download Nordic leaves needed for routes if WORK_DIR set
# and routes_builder_tool exists. Best-effort — report skip if tooling/paths missing.
if [[ -n "$WORK_DIR" && -x "${NAVI_MWM_BUILD_DIR}/routes_builder_tool" ]]; then
  mwm_log "route check: best-effort (temp Nordic MWMs); see open issues if skipped"
  # Full six-route check needs simultaneous presence of many leaves — stream mode
  # cannot keep them. Documented as open issue unless NAVI_MWM_ROUTE_CHECK_LEAVES is set.
  if [[ -n "${NAVI_MWM_ROUTE_CHECK_LEAVES:-}" ]]; then
    mwm_log "NAVI_MWM_ROUTE_CHECK_LEAVES set — operator must stage MWMs under ${WORK_DIR}/route-mwms"
  else
    mwm_log "skip full routes_builder_tool suite in streaming mode (needs multi-leaf MWMs on disk)"
  fi
fi

mwm_log "validate-comaps-release passed"
