#!/usr/bin/env bash
# Nordic RAM matrix: Features-only and/or full e2e under chosen NODE_STORAGE/threads.
# Scratch only. Holds pack-pipeline.lock.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

MODE="features" # features | full
LABEL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --node-storage) export NAVI_MWM_NODE_STORAGE="$2"; shift 2 ;;
    --threads-features) export NAVI_MWM_THREADS_FEATURES="$2"; shift 2 ;;
    --threads-count) export NAVI_MWM_THREADS_COUNT="$2"; shift 2 ;;
    --skip-lock) SKIP_LOCK=1; shift ;;
    *) mwm_die "unknown arg: $1" ;;
  esac
done
SKIP_LOCK="${SKIP_LOCK:-0}"

export NAVI_MWM_COMAPS_ROOT="${NAVI_MWM_COMAPS_ROOT:-/media/navi/haaken-scratch/comaps-feasibility/comaps}"
export NAVI_MWM_BUILD_DIR="${NAVI_MWM_BUILD_DIR:-/media/navi/haaken-scratch/comaps-feasibility/omim-build-release}"
export NAVI_MWM_MAPS_BUILD="${NAVI_MWM_MAPS_BUILD:-/media/navi/haaken-scratch/mwm-ram-maps-build}"
export NAVI_MWM_HGT_CACHE="${NAVI_MWM_HGT_CACHE:-${ROOT}/data/elevation/hgt-mapterhorn}"
export NAVI_MWM_PLANET_PBF="${NAVI_MWM_PLANET_PBF:-/media/navi/haaken-scratch/comaps-feasibility/inputs/scandinavia-norse.osm.pbf}"
export NAVI_MWM_HGT_VOID_FILL="${NAVI_MWM_HGT_VOID_FILL:-1}"
export NAVI_MWM_QT_BIN_DIR="${NAVI_MWM_QT_BIN_DIR:-/media/navi/haaken-scratch/comaps-feasibility/qt6-local/opt/usr/lib/qt6/bin}"
export NAVI_MWM_QT_LIB_DIR="${NAVI_MWM_QT_LIB_DIR:-/media/navi/haaken-scratch/comaps-feasibility/qt6-local/opt/usr/lib/x86_64-linux-gnu}"
export NAVI_MWM_LEAFSET="${NAVI_MWM_LEAFSET:-${ROOT}/config/mwm/leafsets/nordic.txt}"
export NAVI_MWM_PIN_JSON="${ROOT}/config/mwm/comaps-pin.json"
export NAVI_MWM_STATE_DIR="${ROOT}/data/state"
export NAVI_MWM_LOCK="${NAVI_MWM_STATE_DIR}/pack-pipeline.lock"
# Explicit matrix knobs: do not let nordic.json overwrite --node-storage / threads.
export NAVI_MWM_NO_PROFILE=1
export NAVI_MWM_NODE_STORAGE="${NAVI_MWM_NODE_STORAGE:-map}"
export NAVI_MWM_THREADS_FEATURES="${NAVI_MWM_THREADS_FEATURES:-4}"
export NAVI_MWM_THREADS_COUNT="${NAVI_MWM_THREADS_COUNT:-4}"
export NAVI_MWM_ENABLED=0

LABEL="${LABEL:-${NAVI_MWM_NODE_STORAGE}-tf${NAVI_MWM_THREADS_FEATURES}-tc${NAVI_MWM_THREADS_COUNT}-${MODE}}"
mkdir -p "$NAVI_MWM_MAPS_BUILD/navi-logs"
OUT_DIR="${NAVI_MWM_MAPS_BUILD}/navi-logs/ram-${LABEL}"
mkdir -p "$OUT_DIR"

FEATURES_SKIP="Coastline,Descriptions,Mwm,Index,CitiesIdsWorld,RoutingWorld,Ugc,Popularity,PopularityWorld,Srtm,IsolinesInfo,Reviews,Routing,RoutingTransit,MwmDiffs,CountriesTxt,Statistics,Cleanup"

run_inner() {
  mwm_load_defaults
  mwm_apply_profile_from_leafset || true
  # CLI overrides win over profile
  export NAVI_MWM_NODE_STORAGE="${NAVI_MWM_NODE_STORAGE}"
  export NAVI_MWM_THREADS_FEATURES="${NAVI_MWM_THREADS_FEATURES}"
  export NAVI_MWM_THREADS_COUNT="${NAVI_MWM_THREADS_COUNT}"

  {
    echo "label=${LABEL}"
    echo "mode=${MODE}"
    echo "node_storage=${NAVI_MWM_NODE_STORAGE}"
    echo "threads_features=${NAVI_MWM_THREADS_FEATURES}"
    echo "threads_count=${NAVI_MWM_THREADS_COUNT}"
    echo "start_utc=$(date -u -Iseconds)"
  } | tee "${OUT_DIR}/meta.txt"

  if [[ "$MODE" == "features" ]]; then
    export NAVI_MWM_GENERATOR_SKIP="${FEATURES_SKIP}"
  else
    export NAVI_MWM_GENERATOR_SKIP="Coastline"
  fi

  # Peak RSS of generator processes only (not whole user)
  RSS_TSV="${OUT_DIR}/rss.tsv"
  : >"$RSS_TSV"
  (
    while true; do
      # Sum RSS of maps_generator / generator_tool / python maps_generator
      rss=$(ps -u "$(id -un)" -o rss=,comm=,args= 2>/dev/null \
        | awk '/generator_tool|maps_generator|python3 -m maps_generator/ {s+=$1} END{print s+0}')
      echo "$(date -u +%H:%M:%S) gen_rss_kib=$rss" >>"$RSS_TSV"
      sleep 5
    done
  ) &
  SAMP=$!
  trap 'kill $SAMP 2>/dev/null || true' EXIT

  set +e
  /usr/bin/time -v -o "${OUT_DIR}/time.txt" \
    "${SCRIPT_DIR}/run-generator.sh" --skip-lock --force \
    >"${OUT_DIR}/run.log" 2>&1
  EC=$?
  set -e
  kill "$SAMP" 2>/dev/null || true

  peak=$(python3 - "$RSS_TSV" <<'PY2'
from pathlib import Path
import sys
vals=[]
for line in Path(sys.argv[1]).read_text().splitlines():
    if "gen_rss_kib=" in line:
        vals.append(int(line.rsplit("=",1)[-1]))
print(max(vals) if vals else 0)
PY2
)
  {
    echo "exit_code=${EC}"
    echo "end_utc=$(date -u -Iseconds)"
    echo "peak_gen_rss_kib=${peak}"
  } | tee -a "${OUT_DIR}/meta.txt"
  # Stage lines from newest generation.log under maps build
  if [[ -f "${NAVI_MWM_MAPS_BUILD}/generation.log" ]]; then
    rg -n 'Stage .*finished in' "${NAVI_MWM_MAPS_BUILD}/generation.log" \
      | tee "${OUT_DIR}/stages.txt" || true
  fi
  # Disk of newest run dir
  newest=$(ls -1dt "${NAVI_MWM_MAPS_BUILD}"/20* 2>/dev/null | head -1 || true)
  if [[ -n "$newest" ]]; then
    du -sb "$newest" | tee "${OUT_DIR}/disk.txt"
    du -sb "$newest/intermediate_data" 2>/dev/null | tee -a "${OUT_DIR}/disk.txt" || true
    echo "build_dir=$newest" | tee -a "${OUT_DIR}/meta.txt"
  fi
  cat "${OUT_DIR}/time.txt" >>"${OUT_DIR}/meta.txt" || true
  [[ "$EC" -eq 0 ]] || mwm_die "measure failed label=${LABEL} exit=${EC}"

  if [[ "$MODE" == "full" ]]; then
    VERSION_DIR="$(tail -1 "${OUT_DIR}/run.log" | tr -d '\r')"
    [[ -d "$VERSION_DIR" ]] || VERSION_DIR="$(cat "${NAVI_MWM_MAPS_BUILD}/navi-logs/"bake-*-version-dir.txt 2>/dev/null | tail -1)"
    mwm_log "full post: version_dir=${VERSION_DIR}"
    # Ensure altitudes present (zero-fill HGT already configured)
    export NAVI_MWM_SKIP_ANNOTATE=1
    export NAVI_MWM_AMBIGUOUS_THRESHOLD="${NAVI_MWM_AMBIGUOUS_THRESHOLD:-1000000}"
    "${SCRIPT_DIR}/build-sidecars.sh" --version-dir "$VERSION_DIR" --skip-annotate
    "${SCRIPT_DIR}/validate-release.sh" --version-dir "$VERSION_DIR"
    BASELINE="${NAVI_MWM_RAM_BASELINE:-/media/navi/haaken-scratch/mwm-ram-baseline-261111.json}"
    python3 - "$VERSION_DIR" "$BASELINE" "${OUT_DIR}/size-compare.json" <<'PY'
import json, sys
from pathlib import Path
vd = Path(sys.argv[1])
base = json.loads(Path(sys.argv[2]).read_text()) if Path(sys.argv[2]).is_file() else {}
rows = []
for mwm in sorted(vd.glob("*.mwm")):
    leaf = mwm.stem
    sz = mwm.stat().st_size
    b = base.get(leaf)
    bsz = b[0] if isinstance(b, list) else (b if isinstance(b, int) else None)
    rows.append({"leaf": leaf, "bytes": sz, "baseline": bsz, "delta": (sz - bsz) if bsz is not None else None})
Path(sys.argv[3]).write_text(json.dumps(rows, indent=2) + "\n")
diffs = [r for r in rows if r["delta"] not in (0, None)]
print(f"compared={len(rows)} differing_sizes={len(diffs)}")
for r in diffs[:20]:
    print(r["leaf"], "bytes", r["bytes"], "baseline", r["baseline"], "delta", r["delta"])
PY
  fi

  mwm_log "measure OK label=${LABEL} peak_gen_rss_kib=${peak}"
}

mwm_load_defaults
if [[ "$SKIP_LOCK" == "1" ]]; then
  run_inner
else
  mwm_with_lock run_inner
fi
