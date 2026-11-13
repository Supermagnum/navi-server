#!/usr/bin/env bash
# Stage MWMs from CoMaps CDN, run gated + informational routes with wetland hook,
# then delete staged MWMs. Used by validate-comaps-release.sh (default path).
#
# Gates (must pass): six Nordic/border car routes + foot wetland detour.
# Informational (reported, do not fail the gate): US, Japan, Chile-Argentina, Australia.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

STAGE_DIR=""
SIDECAR_DIR=""
ROOT="$(mwm_repo_root)"
LEAFSET="${NAVI_MWM_ROUTE_CHECK_LEAVES:-${ROOT}/config/mwm/leafsets/route-check-extended.txt}"
SKIP_DOWNLOAD=0
KEEP_STAGE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage-dir) STAGE_DIR="$2"; shift 2 ;;
    --sidecar-dir) SIDECAR_DIR="$2"; shift 2 ;;
    --leafset) LEAFSET="$2"; shift 2 ;;
    --skip-download) SKIP_DOWNLOAD=1; shift ;;
    --keep-stage) KEEP_STAGE=1; shift ;;
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
mkdir -p "${STAGE_DIR}/${VER}" "${STAGE_DIR}/navi-routes" "${STAGE_DIR}/navi-dumps"

cleanup_staged_mwms() {
  if [[ "$KEEP_STAGE" == "1" ]]; then
    mwm_log "keeping staged MWMs under ${STAGE_DIR}/${VER} (--keep-stage)"
    return 0
  fi
  mwm_log "deleting staged MWMs under ${STAGE_DIR}/${VER}"
  find "${STAGE_DIR}/${VER}" -maxdepth 1 -type f -name '*.mwm' -delete 2>/dev/null || true
  # Drop hardlinks under navi-route-data too.
  if [[ -d "${STAGE_DIR}/navi-route-data/${VER}" ]]; then
    find "${STAGE_DIR}/navi-route-data/${VER}" -maxdepth 1 -type f -name '*.mwm' -delete 2>/dev/null || true
  fi
}
trap cleanup_staged_mwms EXIT

# Discover + download missing leaves into STAGE_DIR/VER/
if [[ "$SKIP_DOWNLOAD" != "1" ]]; then
  mwm_log "route-validation: ensuring MWMs for leafset=$(basename "$LEAFSET") under ${STAGE_DIR}/${VER}"
  "$PYTHON" - "$SCRIPT_DIR" "$LEAFSET" "${STAGE_DIR}/${VER}" <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.path.join(sys.argv[1], "lib"))
from comaps_release import (
    discover_release,
    download_leaf,
    http_get_bytes,
    sha256_bytes,
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

ROUTE_DATA="${STAGE_DIR}/navi-route-data"
rm -rf "$ROUTE_DATA"
mkdir -p "${ROUTE_DATA}/${VER}"
cp -a "${NAVI_MWM_COMAPS_ROOT}/data/." "$ROUTE_DATA/"
find "$ROUTE_DATA" -name '*.mwm' -type f -delete 2>/dev/null || true
mkdir -p "${ROUTE_DATA}/${VER}"
while IFS= read -r leaf; do
  [[ -z "$leaf" || "$leaf" =~ ^# ]] && continue
  src="${STAGE_DIR}/${VER}/${leaf}.mwm"
  [[ -f "$src" ]] || mwm_die "missing staged MWM: $src"
  ln -f "$src" "${ROUTE_DATA}/${VER}/${leaf}.mwm" 2>/dev/null \
    || cp -a "$src" "${ROUTE_DATA}/${VER}/${leaf}.mwm"
done < <(grep -v '^[[:space:]]*#' "$LEAFSET" | grep -v '^[[:space:]]*$' || true)
if [[ -f "${STAGE_DIR}/${VER}/countries.txt" ]]; then
  cp -a "${STAGE_DIR}/${VER}/countries.txt" "${ROUTE_DATA}/${VER}/"
  cp -a "${STAGE_DIR}/${VER}/countries.txt" "${ROUTE_DATA}/"
fi

export NAVI_WETLAND_SIDECAR_DIR="$SIDECAR_DIR"
ROUTES_DIR="${STAGE_DIR}/navi-routes"
DUMP_ROOT="${STAGE_DIR}/navi-dumps"
mkdir -p "$ROUTES_DIR" "$DUMP_ROOT"
: >"${STAGE_DIR}/route-results.jsonl"

run_route() {
  local name="$1" vehicle="$2" lat1="$3" lon1="$4" lat2="$5" lon2="$6" gate="$7"
  mwm_log "route ${name} vehicle=${vehicle} gate=${gate}"
  echo "${lat1} ${lon1} ${lat2} ${lon2}" >"${ROUTES_DIR}/${name}.txt"
  mkdir -p "${DUMP_ROOT}/${name}"
  set +e
  NAVI_WETLAND_SIDECAR_DIR="$SIDECAR_DIR" \
    "$RB" --data_path="$ROUTE_DATA" --resources_path="$ROUTE_DATA" \
    --routes_file="${ROUTES_DIR}/${name}.txt" \
    --dump_path="${DUMP_ROOT}/${name}" \
    --vehicle_type="$vehicle" --threads=4 --timeout=600 \
    >"${DUMP_ROOT}/${name}/tool.log" 2>&1
  local ec=$?
  set -e
  local dump="${DUMP_ROOT}/${name}/0.mapsme.dump"
  local code="missing_dump"
  local ok=0
  local dist=0
  if [[ -f "$dump" ]]; then
    local parsed
    parsed="$(python3 "${SCRIPT_DIR}/parse_route_dump.py" "$dump" 2>/dev/null || true)"
    code="$(echo "$parsed" | awk '{print $1}')"
    dist="$(echo "$parsed" | sed -n 's/.*distance_m=\([0-9.]*\).*/\1/p')"
    [[ -z "$dist" ]] && dist=0
    if [[ "$code" == "NoError" ]]; then
      ok=1
    fi
  fi
  printf '{"name":"%s","vehicle":"%s","gate":%s,"ok":%s,"code":"%s","distance_m":%s,"exit":%s}\n' \
    "$name" "$vehicle" "$gate" "$ok" "$code" "$dist" "$ec" \
    | tee -a "${STAGE_DIR}/route-results.jsonl"
  [[ "$ok" == "1" ]] || return 1
  return 0
}

GATE_FAIL=0
# --- Gate: six car routes ---
run_route hamar_elverum car 60.7945 11.0680 60.8825 11.5623 true || GATE_FAIL=1
run_route elverum_lillehammer car 60.8825 11.5623 61.1150 10.4660 true || GATE_FAIL=1
run_route hamar_oslo car 60.7945 11.0680 59.9139 10.7522 true || GATE_FAIL=1
run_route lillehammer_drammen car 61.1150 10.4660 59.7440 10.2045 true || GATE_FAIL=1
run_route norway_sweden car 60.8825 11.5623 60.6847 13.7072 true || GATE_FAIL=1
run_route sweden_finland car 65.8355 24.1367 65.8480 24.1466 true || GATE_FAIL=1

# --- Gate: foot wetland detour (expect ~16245 m with Soft×5; baseline 13867) ---
run_route foot_wetland_detour pedestrian 61.0500 11.4700 61.0800 11.5200 true || GATE_FAIL=1
"$PYTHON" - "$STAGE_DIR" <<'PY'
import json, sys
from pathlib import Path
d = Path(sys.argv[1])
rows = [json.loads(l) for l in (d / "route-results.jsonl").read_text().splitlines() if l.strip()]
r = next(x for x in rows if x["name"] == "foot_wetland_detour")
dist = float(r.get("distance_m") or 0)
expect = 16245.0
lo, hi = expect * 0.98, expect * 1.02
ok = bool(r.get("ok")) and lo <= dist <= hi
note = (
    f"foot detour {dist:.1f} m (expect ~{expect} Soft×5; baseline without hook 13867)"
)
row = {
    "name": "foot_wetland_detour_assert",
    "vehicle": "pedestrian",
    "gate": True,
    "ok": ok,
    "code": r.get("code"),
    "distance_m": dist,
    "expect_m": expect,
    "note": note,
    "exit": 0 if ok else 1,
}
with (d / "route-results.jsonl").open("a") as f:
    f.write(json.dumps(row) + "\n")
print(json.dumps(row))
raise SystemExit(0 if ok else 1)
PY
[[ $? == 0 ]] || GATE_FAIL=1

# --- Informational (outside Europe; do not fail the gate) ---
run_route us_la_sacramento car 34.0522 -118.2437 38.5816 -121.4944 false || true
run_route japan_tokyo_yokohama car 35.6812 139.7671 35.4437 139.6380 false || true
run_route chile_argentina car -33.4489 -70.6693 -32.8895 -68.8458 false || true
run_route australia_sydney_melbourne car -33.8688 151.2093 -37.8136 144.9631 false || true

"$PYTHON" - "$STAGE_DIR" <<'PY'
import json, sys
from pathlib import Path
d = Path(sys.argv[1])
rows = [json.loads(l) for l in (d / "route-results.jsonl").read_text().splitlines() if l.strip()]
gates = [r for r in rows if r.get("gate") is True or r.get("gate") == True]
infos = [r for r in rows if not (r.get("gate") is True or r.get("gate") == True)]
# gate True from json bool; foot_assert has gate True
out = {
    "gates": gates,
    "informational": infos,
    "all_gates_ok": all(r.get("ok") for r in gates if r.get("name") != "foot_wetland_detour"),
    # require foot assert row ok
    "foot_ok": next((r.get("ok") for r in gates if r["name"]=="foot_wetland_detour_assert"), False),
    "n_gates": len(gates),
    "n_info": len(infos),
}
out["pass"] = bool(out["all_gates_ok"] and out["foot_ok"])
(d / "route-results.json").write_text(json.dumps(out, indent=2) + "\n")
print(json.dumps(out, indent=2))
raise SystemExit(0 if out["pass"] else 1)
PY
SUMMARY_EC=$?

if [[ "$GATE_FAIL" != "0" || "$SUMMARY_EC" != "0" ]]; then
  mwm_die "route validation gate failed; see ${STAGE_DIR}/route-results.json"
fi
mwm_log "route validation gate OK"
