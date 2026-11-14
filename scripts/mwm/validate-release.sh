#!/usr/bin/env bash
# Post-generator validation before publish. Aborts (exit 1) on any failure.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

VERSION_DIR=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version-dir) VERSION_DIR="$2"; shift 2 ;;
    *) mwm_die "unknown arg: $1" ;;
  esac
done
[[ -n "$VERSION_DIR" && -d "$VERSION_DIR" ]] || mwm_die "need --version-dir"

RB="${NAVI_MWM_BUILD_DIR}/routes_builder_tool"
[[ -x "$RB" ]] || mwm_die "routes_builder_tool missing: $RB"
ROOT="$(mwm_repo_root)"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-${ROOT}/target}"
SIDECAR_BIN="${CARGO_TARGET_DIR}/release/navi-mwm-sidecar"
if [[ ! -x "$SIDECAR_BIN" ]]; then
  cargo build -p navi-mwm-sidecar --release --manifest-path "${ROOT}/Cargo.toml"
fi
[[ -x "$SIDECAR_BIN" ]] || mwm_die "navi-mwm-sidecar binary missing at ${SIDECAR_BIN}"

python3 - "$VERSION_DIR" "$NAVI_MWM_LEAFSET" "$SIDECAR_BIN" <<'PY'
import json, subprocess, sys
from pathlib import Path

version = Path(sys.argv[1])
leafset = Path(sys.argv[2])
sidecar_bin = sys.argv[3]
leaves = [ln.strip() for ln in leafset.read_text().splitlines() if ln.strip() and not ln.strip().startswith("#")]
errors = []

REQUIRED = (b"routing", b"cross_mwm", b"altitudes")

def has_sections(mwm: Path) -> list[str]:
    data = mwm.read_bytes()
    return [name.decode() for name in REQUIRED if name not in data]

for leaf in leaves:
    mwm = version / f"{leaf}.mwm"
    sc = version / f"{leaf}.navi-sidecar.v1.zst"
    if not mwm.is_file():
        errors.append(f"missing mwm {leaf}")
        continue
    miss = has_sections(mwm)
    if miss:
        errors.append(f"{leaf} missing sections: {miss}")
    if not sc.is_file():
        errors.append(f"missing sidecar {leaf}")
        continue
    proc = subprocess.run(
        [sidecar_bin, "inspect", "--sidecar", str(sc), "--mwm", str(mwm)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        errors.append(f"{leaf} sidecar inspect failed: {proc.stderr.strip()}")
        continue
    meta = json.loads(proc.stdout)
    if not meta.get("fingerprint_ok", False):
        errors.append(f"{leaf} sidecar fingerprint mismatch vs mwm")

attr = version / "attribution.json"
if not attr.is_file():
    errors.append("missing attribution.json")
else:
    data = json.loads(attr.read_text())
    blob = json.dumps(data).lower()
    if "mapterhorn" not in blob:
        errors.append("attribution.json must name Mapterhorn (no generic elevation-only label)")

if errors:
    for e in errors:
        print("ERROR:", e)
    raise SystemExit(1)
print(f"section+sidecar checks OK leaves={len(leaves)}")
PY

# Prepare routes_builder data layout: resources + {version}/ MWMs (same run only).
VER_NAME="$(basename "$VERSION_DIR")"
ROUTE_DATA="${VERSION_DIR}/navi-route-data"
rm -rf "$ROUTE_DATA"
mkdir -p "${ROUTE_DATA}/${VER_NAME}"
# Minimal resources: copy countries + icudt etc from comaps data (large but correct).
# Prefer hardlink/symlink MWMs from this version only.
cp -a "${NAVI_MWM_COMAPS_ROOT}/data/." "$ROUTE_DATA/"
# Remove any pre-existing regional MWMs under version dirs to avoid mixing.
find "$ROUTE_DATA" -name '*.mwm' -type f -delete 2>/dev/null || true
mkdir -p "${ROUTE_DATA}/${VER_NAME}"
while IFS= read -r leaf; do
  [[ -f "${VERSION_DIR}/${leaf}.mwm" ]] || continue
  ln -f "${VERSION_DIR}/${leaf}.mwm" "${ROUTE_DATA}/${VER_NAME}/${leaf}.mwm" 2>/dev/null \
    || cp -a "${VERSION_DIR}/${leaf}.mwm" "${ROUTE_DATA}/${VER_NAME}/${leaf}.mwm"
done < <(mwm_read_leafset)
if [[ -f "${VERSION_DIR}/countries.txt" ]]; then
  cp -a "${VERSION_DIR}/countries.txt" "${ROUTE_DATA}/${VER_NAME}/"
  cp -a "${VERSION_DIR}/countries.txt" "${ROUTE_DATA}/"
fi

ROUTES_DIR="${VERSION_DIR}/navi-validate-routes"
DUMP_ROOT="${VERSION_DIR}/navi-validate-dumps"
mkdir -p "$ROUTES_DIR" "$DUMP_ROOT"

run_route() {
  local name="$1" lat1="$2" lon1="$3" lat2="$4" lon2="$5"
  mwm_log "route ${name}"
  echo "${lat1} ${lon1} ${lat2} ${lon2}" >"${ROUTES_DIR}/${name}.txt"
  mkdir -p "${DUMP_ROOT}/${name}"
  set +e
  "$RB" --data_path="$ROUTE_DATA" --resources_path="$ROUTE_DATA" \
    --routes_file="${ROUTES_DIR}/${name}.txt" \
    --dump_path="${DUMP_ROOT}/${name}" \
    --vehicle_type=car --threads=4 --timeout=600 \
    >"${DUMP_ROOT}/${name}/tool.log" 2>&1
  local ec=$?
  set -e
  local dump="${DUMP_ROOT}/${name}/0.mapsme.dump"
  [[ -f "$dump" ]] || mwm_die "route ${name} missing dump; log=${DUMP_ROOT}/${name}/tool.log"
  # Dump encodes RouterResultCode; NoError == 0.
  local parser="${SCRIPT_DIR}/parse_route_dump.py"
  python3 "$parser" "$dump" --require-no-error \
    || mwm_die "route ${name} not NoError; dump=${dump}"
  mwm_log "route ${name} OK"
}

run_route hamar_elverum 60.7945 11.0680 60.8825 11.5623
run_route elverum_lillehammer 60.8825 11.5623 61.1150 10.4660
run_route hamar_oslo 60.7945 11.0680 59.9139 10.7522
run_route lillehammer_drammen 61.1150 10.4660 59.7440 10.2045
run_route norway_sweden 60.8825 11.5623 60.6847 13.7072
run_route sweden_finland 65.8355 24.1367 65.8480 24.1466

mwm_log "validate-release OK"
