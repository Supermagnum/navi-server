#!/usr/bin/env bash
# Run one CoMaps generator bake for the configured leaf set.
# Respects pack-pipeline.lock. Logs per-stage times, peak RAM, disk.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

SKIP_LOCK=0
FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-lock) SKIP_LOCK=1; shift ;;
    --force) FORCE=1; shift ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *) mwm_die "unknown arg: $1" ;;
  esac
done

if [[ "${NAVI_MWM_ENABLED}" != "1" && "$FORCE" != "1" ]]; then
  mwm_die "NAVI_MWM_ENABLED=0 (pass --force for scratch runs)"
fi

[[ -n "${NAVI_MWM_PLANET_PBF}" ]] || mwm_die "NAVI_MWM_PLANET_PBF unset"
[[ -f "${NAVI_MWM_PLANET_PBF}" ]] || mwm_die "PBF missing: ${NAVI_MWM_PLANET_PBF}"
[[ -d "${NAVI_MWM_COMAPS_ROOT}" ]] || mwm_die "comaps root missing; run build-generator.sh first"
[[ -x "${NAVI_MWM_BUILD_DIR}/generator_tool" ]] || mwm_die "generator_tool missing"

TAG="$(mwm_pin_field tag)"
SHA="$(mwm_pin_field sha)"
RUN_ID="$(date -u +%Y_%m_%d__%H_%M_%S)"
LOG_DIR="${NAVI_MWM_MAPS_BUILD}/navi-logs"
mkdir -p "$LOG_DIR" "$NAVI_MWM_HGT_CACHE"
LOG="${LOG_DIR}/bake-${RUN_ID}.log"
METRICS="${LOG_DIR}/bake-${RUN_ID}-metrics.txt"
RSS_TSV="${LOG_DIR}/bake-${RUN_ID}-rss.tsv"
OVERLAY="${NAVI_MWM_MAPS_BUILD}/overlays/${RUN_ID}"

run_inner() {
  mwm_log "run-generator start run_id=${RUN_ID} leafset=${NAVI_MWM_LEAFSET}"
  python3 "${SCRIPT_DIR}/validate-leafset.py" \
    --leafset "$NAVI_MWM_LEAFSET" \
    --borders-dir "${NAVI_MWM_COMAPS_ROOT}/data/borders" \
    --json-out "${LOG_DIR}/leafset-${RUN_ID}.json"

  python3 "${SCRIPT_DIR}/prepare-countries-overlay.py" \
    --leafset "$NAVI_MWM_LEAFSET" \
    --borders-dir "${NAVI_MWM_COMAPS_ROOT}/data/borders" \
    --overlay "$OVERLAY" \
    --map-series "navi-$(basename "$NAVI_MWM_LEAFSET" .txt)"

  python3 "${SCRIPT_DIR}/validate-leafset.py" \
    --leafset "$NAVI_MWM_LEAFSET" \
    --borders-dir "${NAVI_MWM_COMAPS_ROOT}/data/borders" \
    --overlay-borders "${OVERLAY}/borders" \
    --overlay-countries "${OVERLAY}/countries.txt"

  # Install countries.txt into user resource path (restore on exit).
  COUNTRIES_DST="${NAVI_MWM_COMAPS_ROOT}/data/countries.txt"
  COUNTRIES_BAK="${COUNTRIES_DST}.navi-bak-${RUN_ID}"
  cp -a "$COUNTRIES_DST" "$COUNTRIES_BAK"
  cp -a "${OVERLAY}/countries.txt" "$COUNTRIES_DST"
  restore_countries() {
    if [[ -f "$COUNTRIES_BAK" ]]; then
      mv -f "$COUNTRIES_BAK" "$COUNTRIES_DST"
      mwm_log "restored countries.txt from backup"
    fi
  }
  trap restore_countries EXIT

  # HGT: ensure cache covers leafset (void-fill allowed if NAVI_MWM_HGT_VOID_FILL=1)
  ATTR_OUT="${OVERLAY}/mapterhorn-attribution.json"
  HGT_ARGS=(
    --out "$NAVI_MWM_HGT_CACHE"
    --manifest "${OVERLAY}/hgt-manifest.json"
    --attribution-out "$ATTR_OUT"
    --attribution-url "${NAVI_MWM_MAPTERHORN_ATTRIBUTION_URL}"
    --leafset "$NAVI_MWM_LEAFSET"
    --borders-dir "${NAVI_MWM_COMAPS_ROOT}/data/borders"
  )
  ATTR_CACHE="$(mwm_repo_root)/config/mwm/mapterhorn-attribution.json"
  if [[ -f "$ATTR_CACHE" ]]; then
    HGT_ARGS+=(--attribution-json "$ATTR_CACHE")
  fi
  if [[ -n "${NAVI_MWM_MAPTERHORN_PMTILES}" && -f "${NAVI_MWM_MAPTERHORN_PMTILES}" ]]; then
    HGT_ARGS+=(--pmtiles "$NAVI_MWM_MAPTERHORN_PMTILES")
  fi
  if [[ "${NAVI_MWM_HGT_VOID_FILL:-0}" == "1" ]]; then
    # Prefer zero-fill for scratch so CoMaps can emit altitudes sections
    # (pure SRTM voids yield "No altitude information" and omit the section).
    HGT_ARGS+=(--allow-void-fill --zero-fill --keep-existing)
  fi
  HGT_PY="${NAVI_MWM_HGT_PYTHON:-python3}"
  "$HGT_PY" "${SCRIPT_DIR}/mapterhorn-to-hgt.py" "${HGT_ARGS[@]}"

  # Patch map_generator.ini for this run (write a run-local copy + symlink trick).
  INI_SRC="${NAVI_MWM_COMAPS_ROOT}/tools/python/maps_generator/var/etc/map_generator.ini"
  INI_RUN="${OVERLAY}/map_generator.ini"
  PBF_URL="file://${NAVI_MWM_PLANET_PBF}"
  MD5_FILE="${NAVI_MWM_PLANET_PBF}.md5"
  if [[ ! -f "$MD5_FILE" ]]; then
    md5sum "$NAVI_MWM_PLANET_PBF" | awk '{print $1}' >"$MD5_FILE"
  fi
  python3 - "$INI_SRC" "$INI_RUN" "$NAVI_MWM_COMAPS_ROOT" "$NAVI_MWM_BUILD_DIR" \
    "$NAVI_MWM_MAPS_BUILD" "$PBF_URL" "file://${MD5_FILE}" "$NAVI_MWM_HGT_CACHE" \
    "${NAVI_MWM_THREADS_FEATURES}" "${NAVI_MWM_NODE_STORAGE}" <<'PY'
import re, sys
src, dst, omim, build, out, planet, md5, srtm, threads, node = sys.argv[1:]
text = open(src).read()
def set_key(text, key, value):
    # Replace first KEY: value occurrence in file
    pat = re.compile(rf'(?m)^({re.escape(key)}\s*:\s*).*$')
    if pat.search(text):
        return pat.sub(rf'\g<1>{value}', text, count=1)
    return text + f"\n{key}: {value}\n"
text = set_key(text, 'OMIM_PATH', omim)
text = set_key(text, 'BUILD_PATH', build)
text = set_key(text, 'MAIN_OUT_PATH', out)
text = set_key(text, 'USER_RESOURCE_PATH', omim + '/data')
text = set_key(text, 'PLANET_URL', planet)
text = set_key(text, 'PLANET_MD5_URL', md5)
text = set_key(text, 'SRTM_PATH', srtm)
text = set_key(text, 'THREADS_COUNT_FEATURES_STAGE', threads)
text = set_key(text, 'NODE_STORAGE', node)
text = set_key(text, 'NEED_PLANET_UPDATE', '0')
open(dst, 'w').write(text)
print('wrote', dst)
PY

  # maps_generator reads var/etc/map_generator.ini — swap temporarily
  INI_BAK="${INI_SRC}.navi-bak-${RUN_ID}"
  cp -a "$INI_SRC" "$INI_BAK"
  cp -a "$INI_RUN" "$INI_SRC"
  restore_ini() {
    if [[ -f "$INI_BAK" ]]; then
      mv -f "$INI_BAK" "$INI_SRC"
      mwm_log "restored map_generator.ini"
    fi
    restore_countries
  }
  trap restore_ini EXIT

  COUNTRIES="$(mwm_countries_csv)"
  DISK_BASE=$(df -B1 "$(dirname "$NAVI_MWM_MAPS_BUILD")" | awk 'NR==2{print $3}')
  {
    echo "start_utc=$(date -u -Iseconds)"
    echo "run_id=${RUN_ID}"
    echo "comaps_tag=${TAG}"
    echo "comaps_sha=${SHA}"
    echo "planet=${NAVI_MWM_PLANET_PBF}"
    echo "srtm_path=${NAVI_MWM_HGT_CACHE}"
    echo "countries=${COUNTRIES}"
    echo "disk_used_bytes_start=${DISK_BASE}"
    echo "threads_features=${NAVI_MWM_THREADS_FEATURES}"
    echo "node_storage=${NAVI_MWM_NODE_STORAGE}"
    echo "memory_max_hint=${NAVI_MWM_MEMORY_MAX}"
  } | tee "$METRICS"

  : >"$RSS_TSV"
  (
    while true; do
      rss=$(ps -u "$(id -un)" -o rss= 2>/dev/null | awk '{s+=$1} END{print s+0}')
      echo "$(date -u +%H:%M:%S) rss_kib_all_user=$rss" >>"$RSS_TSV"
      sleep 5
    done
  ) &
  SAMP=$!
  cleanup_samp() { kill "$SAMP" 2>/dev/null || true; restore_ini; }
  trap cleanup_samp EXIT

  # Activate maps_generator venv if present
  if [[ -f "${NAVI_MWM_COMAPS_ROOT}/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "${NAVI_MWM_COMAPS_ROOT}/.venv/bin/activate"
  fi
  export PATH="${NAVI_MWM_QT_BIN_DIR:-}:${PATH:-}"
  export LD_LIBRARY_PATH="${NAVI_MWM_QT_LIB_DIR:-}:${LD_LIBRARY_PATH:-}"

  cd "${NAVI_MWM_COMAPS_ROOT}/tools/python"
  set +e
  /usr/bin/time -v -o "${LOG_DIR}/bake-${RUN_ID}-time.txt" \
    python3 -m maps_generator --countries="$COUNTRIES" --skip="Coastline" \
    >"$LOG" 2>&1
  EC=$?
  set -e
  kill "$SAMP" 2>/dev/null || true

  DISK_END=$(df -B1 "$(dirname "$NAVI_MWM_MAPS_BUILD")" | awk 'NR==2{print $3}')
  peak=$(awk '{print $NF}' "$RSS_TSV" | sort -n | tail -1)
  {
    echo "exit_code=${EC}"
    echo "end_utc=$(date -u -Iseconds)"
    echo "disk_used_bytes_end=${DISK_END}"
    echo "disk_delta_bytes=$((DISK_END - DISK_BASE))"
    echo "peak_user_rss_kib=${peak}"
    echo "log=${LOG}"
  } | tee -a "$METRICS"
  if [[ -f "${LOG_DIR}/bake-${RUN_ID}-time.txt" ]]; then
    cat "${LOG_DIR}/bake-${RUN_ID}-time.txt" >>"$METRICS"
  fi

  # Parse stage timings from generation.log if present
  if [[ -f "${NAVI_MWM_MAPS_BUILD}/generation.log" ]]; then
    python3 - <<PY >>"$METRICS" || true
import re
from pathlib import Path
log = Path("${NAVI_MWM_MAPS_BUILD}/generation.log").read_text(errors="replace")
# Best-effort: StageX finished / took patterns vary; keep raw mentions
for m in re.finditer(r"(Stage\w+).*?(?:finished|took|done).*", log, re.I):
    print("stage_line=" + m.group(0)[:200])
PY
  fi

  # Locate newest version dir containing our leaves
  VERSION_DIR="$(python3 - <<PY
from pathlib import Path
root = Path("${NAVI_MWM_MAPS_BUILD}")
leaf = "$(mwm_read_leafset | head -1)"
cands = sorted(root.glob(f"*/{leaf}.mwm"), key=lambda p: p.stat().st_mtime, reverse=True)
if not cands:
    cands = sorted(root.glob(f"*/*/{leaf}.mwm"), key=lambda p: p.stat().st_mtime, reverse=True)
if not cands:
    raise SystemExit("no mwm found for " + leaf)
# maps_build/<run>/<version>/<leaf>.mwm
p = cands[0]
if p.parent.name.isdigit() or len(p.parent.name) == 6:
    print(p.parent)
else:
    print(p.parent)
PY
)"
  echo "version_dir=${VERSION_DIR}" | tee -a "$METRICS"
  echo "${VERSION_DIR}" >"${LOG_DIR}/bake-${RUN_ID}-version-dir.txt"
  cp -a "$ATTR_OUT" "${VERSION_DIR}/attribution-mapterhorn.json" 2>/dev/null || true

  [[ "$EC" -eq 0 ]] || mwm_die "maps_generator failed exit=${EC} log=${LOG}"
  mwm_log "run-generator OK version_dir=${VERSION_DIR}"
  echo "$VERSION_DIR"
}

if [[ "$SKIP_LOCK" == "1" ]]; then
  run_inner
else
  mwm_with_lock run_inner
fi
