#!/usr/bin/env bash
# Stage configurable MWMs and run the six-route suite with the wetland hook.
# Uses sectioned NVSC sidecars (NAVI_WETLAND_SIDECAR_DIR). No live publish.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

STAGE_DIR=""
SIDECAR_DIR=""
ROOT="$(mwm_repo_root)"
LEAFSET="${NAVI_MWM_ROUTE_CHECK_LEAVES:-${ROOT}/config/mwm/leafsets/six-route.txt}"
SKIP_DOWNLOAD=0
VEHICLE=car
while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage-dir) STAGE_DIR="$2"; shift 2 ;;
    --sidecar-dir) SIDECAR_DIR="$2"; shift 2 ;;
    --leafset) LEAFSET="$2"; shift 2 ;;
    --skip-download) SKIP_DOWNLOAD=1; shift ;;
    --vehicle) VEHICLE="$2"; shift 2 ;;
    *) mwm_die "unknown arg: $1" ;;
  esac
done

[[ -n "$STAGE_DIR" ]] || mwm_die "need --stage-dir"
[[ -n "$SIDECAR_DIR" ]] || mwm_die "need --sidecar-dir (sectioned .navi-sidecar.v1.zst)"
[[ -f "$LEAFSET" ]] || mwm_die "leafset missing: $LEAFSET"
RB="${NAVI_MWM_BUILD_DIR}/routes_builder_tool"
[[ -x "$RB" ]] || mwm_die "routes_builder_tool missing: $RB"
PYTHON="${NAVI_MWM_PYTHON:-python3}"
if [[ -x /media/navi/haaken-scratch/mwm-comaps-scratch/venv/bin/python ]]; then
  PYTHON="${NAVI_MWM_PYTHON:-/media/navi/haaken-scratch/mwm-comaps-scratch/venv/bin/python}"
fi

VER="${NAVI_MWM_COMAPS_VERSION:-261105}"
mkdir -p "${STAGE_DIR}/${VER}" "${STAGE_DIR}/navi-six-routes" "${STAGE_DIR}/navi-six-dumps"

# Discover + download missing leaves into STAGE_DIR/VER/
if [[ "$SKIP_DOWNLOAD" != "1" ]]; then
  mwm_log "six-route: ensuring MWMs for leafset=$(basename "$LEAFSET") under ${STAGE_DIR}/${VER}"
  "$PYTHON" - "$SCRIPT_DIR" "$LEAFSET" "${STAGE_DIR}/${VER}" <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.path.join(sys.argv[1], "lib"))
from comaps_release import (
    discover_release,
    download_leaf,
    http_get_bytes,
    leaf_mwm_url,
    DEFAULT_METASERVER,
    DEFAULT_MAP_SERIES,
)

leafset = Path(sys.argv[2])
out = Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
wanted = [
    ln.strip()
    for ln in leafset.read_text().splitlines()
    if ln.strip() and not ln.strip().startswith("#")
]
meta = os.environ.get("NAVI_MWM_METASERVER", DEFAULT_METASERVER)
series = os.environ.get("NAVI_MWM_MAP_SERIES", DEFAULT_MAP_SERIES)
ver = os.environ.get("NAVI_MWM_COMAPS_VERSION")
info = discover_release(
    metaserver=meta,
    map_series=series,
    data_version=int(ver) if ver else None,
)
# Re-fetch countries.txt body (ReleaseInfo keeps hash/size only).
countries_url = leaf_mwm_url(info.mirrors[0], info.map_series, info.version, "countries").replace(
    "countries.mwm", "countries.txt"
)
# Prefer published/mirror countries.txt pattern used by discover_release.
from comaps_release import sha256_bytes  # noqa: E402
raw = None
for mirror in info.mirrors:
    base = mirror if mirror.endswith("/") else mirror + "/"
    url = f"{base}maps/{info.map_series}/{info.version}/countries.txt"
    try:
        raw = http_get_bytes(url, timeout=60.0)
        if sha256_bytes(raw) == info.countries_txt_sha256:
            break
    except Exception:
        raw = None
if raw is None:
    raise SystemExit("failed to fetch countries.txt matching discovery hash")
(out / "countries.txt").write_bytes(raw)

missing = [l for l in wanted if l not in info.leaves]
if missing:
    raise SystemExit(f"leaves not in countries.txt: {missing}")
for name in wanted:
    dest = out / f"{name}.mwm"
    if dest.is_file() and dest.stat().st_size > 0:
        print(f"have {name}", flush=True)
        continue
    print(f"download {name}…", flush=True)
    download_leaf(
        mirrors=info.mirrors,
        map_series=info.map_series,
        version=info.version,
        leaf=info.leaves[name],
        dest=dest,
    )
print(json.dumps({"staged": len(wanted), "version": info.version}))
PY
fi

# routes_builder data layout: resources + {ver}/ MWMs
ROUTE_DATA="${STAGE_DIR}/navi-route-data"
rm -rf "$ROUTE_DATA"
mkdir -p "${ROUTE_DATA}/${VER}"
cp -a "${NAVI_MWM_COMAPS_ROOT}/data/." "$ROUTE_DATA/"
find "$ROUTE_DATA" -name '*.mwm' -type f -delete 2>/dev/null || true
mkdir -p "${ROUTE_DATA}/${VER}"
while IFS= read -r leaf; do
  [[ -z "$leaf" ]] && continue
  src="${STAGE_DIR}/${VER}/${leaf}.mwm"
  [[ -f "$src" ]] || mwm_die "missing staged MWM: $src"
  ln -f "$src" "${ROUTE_DATA}/${VER}/${leaf}.mwm" 2>/dev/null \
    || cp -a "$src" "${ROUTE_DATA}/${VER}/${leaf}.mwm"
done < <(mwm_read_leafset "$LEAFSET")
if [[ -f "${STAGE_DIR}/${VER}/countries.txt" ]]; then
  cp -a "${STAGE_DIR}/${VER}/countries.txt" "${ROUTE_DATA}/${VER}/"
  cp -a "${STAGE_DIR}/${VER}/countries.txt" "${ROUTE_DATA}/"
fi

export NAVI_WETLAND_SIDECAR_DIR="$SIDECAR_DIR"
ROUTES_DIR="${STAGE_DIR}/navi-six-routes"
DUMP_ROOT="${STAGE_DIR}/navi-six-dumps"
mkdir -p "$ROUTES_DIR" "$DUMP_ROOT"
RESULTS="${STAGE_DIR}/six-route-results.json"
: >"${STAGE_DIR}/six-route-results.jsonl"

run_route() {
  local name="$1" lat1="$2" lon1="$3" lat2="$4" lon2="$5"
  mwm_log "six-route ${name} vehicle=${VEHICLE}"
  echo "${lat1} ${lon1} ${lat2} ${lon2}" >"${ROUTES_DIR}/${name}.txt"
  mkdir -p "${DUMP_ROOT}/${name}"
  set +e
  NAVI_WETLAND_SIDECAR_DIR="$SIDECAR_DIR" \
    "$RB" --data_path="$ROUTE_DATA" --resources_path="$ROUTE_DATA" \
    --routes_file="${ROUTES_DIR}/${name}.txt" \
    --dump_path="${DUMP_ROOT}/${name}" \
    --vehicle_type="$VEHICLE" --threads=4 --timeout=600 \
    >"${DUMP_ROOT}/${name}/tool.log" 2>&1
  local ec=$?
  set -e
  local dump="${DUMP_ROOT}/${name}/0.mapsme.dump"
  local code="missing_dump"
  local ok=0
  if [[ -f "$dump" ]]; then
    if python3 "${SCRIPT_DIR}/parse_route_dump.py" "$dump" --require-no-error >/dev/null 2>&1; then
      code="NoError"
      ok=1
    else
      code="$(python3 "${SCRIPT_DIR}/parse_route_dump.py" "$dump" 2>/dev/null | head -1 || echo parse_fail)"
    fi
  fi
  printf '{"name":"%s","ok":%s,"code":"%s","exit":%s}\n' "$name" "$ok" "$code" "$ec" \
    | tee -a "${STAGE_DIR}/six-route-results.jsonl"
  [[ "$ok" == "1" ]] || return 1
  return 0
}

FAIL=0
run_route hamar_elverum 60.7945 11.0680 60.8825 11.5623 || FAIL=1
run_route elverum_lillehammer 60.8825 11.5623 61.1150 10.4660 || FAIL=1
run_route hamar_oslo 60.7945 11.0680 59.9139 10.7522 || FAIL=1
run_route lillehammer_drammen 61.1150 10.4660 59.7440 10.2045 || FAIL=1
run_route norway_sweden 60.8825 11.5623 60.6847 13.7072 || FAIL=1
run_route sweden_finland 65.8355 24.1367 65.8480 24.1466 || FAIL=1

"$PYTHON" - "$STAGE_DIR" <<'PY'
import json, sys
from pathlib import Path
d = Path(sys.argv[1])
rows = [json.loads(l) for l in (d / "six-route-results.jsonl").read_text().splitlines() if l.strip()]
out = {"routes": rows, "all_no_error": all(r.get("ok") for r in rows), "n": len(rows)}
(d / "six-route-results.json").write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps(out, indent=2))
PY

if [[ "$FAIL" != "0" ]]; then
  mwm_die "six-route check failed; see ${STAGE_DIR}/six-route-results.json"
fi
mwm_log "six-route check OK"
