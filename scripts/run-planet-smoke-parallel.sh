#!/usr/bin/env bash
# Concurrent planet-smoke runner (CPU/RAM-aware).
#
# Does NOT touch an already-running sequential ./run-planet-smoke.sh — use this
# for a fresh or later run (typically with --resume after the sequential job
# finishes, or on a leased server).
#
# Safeguards:
#   - collision-proof generation_id (common.sh)
#   - flock-serialized publish; no cross-gen rm -rf of in-flight dirs
#   - DEM tile leases + download lockfiles
#   - quota re-check before each convert dispatch
#   - large-DEM regions always serial; small/medium share a worker pool sized
#     from nproc + MemAvailable with configurable reserves
#
# Usage:
#   ./run-planet-smoke-parallel.sh --dry-run --limit 4
#   ./run-planet-smoke-parallel.sh --resume
#   ./run-planet-smoke-parallel.sh --only andorra,europe_albania --jobs 2
#
# Config (env / config.env):
#   NAVI_SMOKE_QUOTA_ABORT_PCT     default 90
#   NAVI_SMOKE_RESERVE_CORES       cores left for co-resident services (default 2)
#   NAVI_SMOKE_RESERVE_RAM_GIB     GiB left free (default 8)
#   NAVI_SMOKE_THREADS_PER_WORKER  threads assumed per convert (default 4)
#   NAVI_SMOKE_RSS_GIB_PER_WORKER  peak RSS budget per convert (default 6)
#   NAVI_SMOKE_MAX_JOBS            hard ceiling (0 = no extra ceiling)
#   NAVI_SMOKE_LARGE_DEM_CELLS     serial if bbox cells > this (default 150)
#   NAVI_SMOKE_EVICT_DEM           1 to evict after publish (default 1)

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/publish-safe.sh
source "${SCRIPT_DIR}/lib/publish-safe.sh"
load_config

RESUME=0
LIMIT=0
ONLY=""
DRY_RUN=0
JOBS_OVERRIDE=""
ABORT_PCT="${NAVI_SMOKE_QUOTA_ABORT_PCT:-90}"
REPORT_DIR="${NAVI_LOG_DIR}/planet-smoke-parallel"
STATE_FILE="${REPORT_DIR}/state.jsonl"
SUMMARY_FILE="${REPORT_DIR}/summary.json"
REGIONS_FILE="${NAVI_PACK_ROOT}/regions.planet.conf"
BBOX_FILE="${REGIONS_FILE}.bboxes.json"
STOP_FILE="${NAVI_PACK_ROOT}/STOP_SMOKE"
START_EPOCH="$(date -u +%s)"
WORKER_SLOT_DIR=""

smoke_stop_requested() {
  [[ -f "$STOP_FILE" ]]
}

mkdir -p "$REPORT_DIR" "${NAVI_PUBLISHED_DIR}/packs"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --resume) RESUME=1; shift ;;
    --limit) LIMIT="$2"; shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    --jobs) JOBS_OVERRIDE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
done

export NAVI_BAKE_DELTA_H=1
export NAVI_BAKE_TOWN_ROUTES=0
export NAVI_REGIONS_CONF="$REGIONS_FILE"
: "${NAVI_ELEV_DIR:=${NAVI_PACK_ROOT}/elevation}"
export NAVI_ELEV_DIR
load_config

quota_pct() {
  local used quota
  used="$(zfs get -Hp -o value used "$NAVI_ZFS_DATASET")"
  quota="$(zfs get -Hp -o value quota "$NAVI_ZFS_DATASET")"
  if [[ "$quota" == "0" || "$quota" == "none" ]]; then
    echo 0
    return
  fi
  awk -v u="$used" -v q="$quota" 'BEGIN{printf "%.1f", (u*100)/q}'
}

quota_abort_if_needed() {
  local pct phase="$1"
  pct="$(quota_pct)"
  log_info "quota check phase=${phase} used_pct=${pct}% abort_at=${ABORT_PCT}%"
  if awk -v u="$pct" -v a="$ABORT_PCT" 'BEGIN{exit !(u+0 >= a+0)}'; then
    log_fail "QUOTA ABORT at ${pct}% (>= ${ABORT_PCT}%) during ${phase}"
    state_append "{\"event\":\"quota_abort\",\"phase\":\"${phase}\",\"used_pct\":${pct}}"
    return 2
  fi
  return 0
}

state_append() {
  local line="$1"
  mkdir -p "$(dirname "$STATE_FILE")"
  (
    flock 8
    printf '%s\n' "$line" >>"$STATE_FILE"
  ) 8>>"${STATE_FILE}.lock"
}

detect_concurrency() {
  local threads mem_gib reserve_cores reserve_ram threads_per rss_per ceiling
  local usable_threads usable_ram by_cpu by_ram jobs

  threads="$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
  mem_gib="$(awk '/MemAvailable:/ {printf "%.1f", $2/1024/1024}' /proc/meminfo)"
  [[ -n "$mem_gib" ]] || mem_gib=4

  reserve_cores="${NAVI_SMOKE_RESERVE_CORES:-2}"
  reserve_ram="${NAVI_SMOKE_RESERVE_RAM_GIB:-8}"
  threads_per="${NAVI_SMOKE_THREADS_PER_WORKER:-4}"
  rss_per="${NAVI_SMOKE_RSS_GIB_PER_WORKER:-6}"
  ceiling="${NAVI_SMOKE_MAX_JOBS:-0}"

  usable_threads="$(awk -v t="$threads" -v r="$reserve_cores" 'BEGIN{v=t-r; if(v<1)v=1; print int(v)}')"
  usable_ram="$(awk -v m="$mem_gib" -v r="$reserve_ram" 'BEGIN{v=m-r; if(v<1)v=1; printf "%.1f", v}')"
  by_cpu="$(awk -v u="$usable_threads" -v p="$threads_per" 'BEGIN{v=int(u/p); if(v<1)v=1; print v}')"
  by_ram="$(awk -v u="$usable_ram" -v p="$rss_per" 'BEGIN{v=int(u/p); if(v<1)v=1; print v}')"
  jobs="$by_cpu"
  [[ "$by_ram" -lt "$jobs" ]] && jobs="$by_ram"
  if [[ "$ceiling" -gt 0 && "$ceiling" -lt "$jobs" ]]; then
    jobs="$ceiling"
  fi
  if [[ -n "$JOBS_OVERRIDE" ]]; then
    jobs="$JOBS_OVERRIDE"
  fi
  [[ "$jobs" -lt 1 ]] && jobs=1

  DETECTED_THREADS="$threads"
  DETECTED_MEM_GIB="$mem_gib"
  DETECTED_RESERVE_CORES="$reserve_cores"
  DETECTED_RESERVE_RAM_GIB="$reserve_ram"
  DETECTED_THREADS_PER="$threads_per"
  DETECTED_RSS_PER="$rss_per"
  DETECTED_CEILING="$ceiling"
  DETECTED_BY_CPU="$by_cpu"
  DETECTED_BY_RAM="$by_ram"
  DETECTED_JOBS="$jobs"
}

region_dem_cells() {
  local rid="$1"
  [[ -f "$BBOX_FILE" ]] || { echo 0; return; }
  python3 -c "
import json, math, sys
d=json.load(open(sys.argv[1]))
b=d.get(sys.argv[2])
if not b:
  print(0); raise SystemExit
cells=(math.floor(b[2])-math.floor(b[0])+1)*(math.floor(b[3])-math.floor(b[1])+1)
print(cells)
" "$BBOX_FILE" "$rid"
}

already_done() {
  local rid="$1"
  [[ -d "${NAVI_PUBLISHED_DIR}/packs/${rid}" ]] || return 1
  find "${NAVI_PUBLISHED_DIR}/packs/${rid}" -mindepth 1 -maxdepth 1 -type d \
    -exec test -f '{}/manifest.json' ';' -print -quit | grep -q .
}

cleanup_region_scratch() {
  local rid="$1"
  local pbf
  pbf="$(region_pbf_path "$rid")"
  rm -f "$pbf" "${pbf}.md5" "${pbf}.partial"
  rm -rf "${NAVI_CONVERT_DIR}/${rid}"
  log_info "cleaned scratch for ${rid}"
}

region_bbox_csv() {
  local rid="$1"
  [[ -f "$BBOX_FILE" ]] || { echo ""; return; }
  python3 -c "
import json,sys
d=json.load(open(sys.argv[1])); b=d.get(sys.argv[2])
print(','.join(map(str,b)) if b else '')
" "$BBOX_FILE" "$rid"
}

# Process one region end-to-end. Exit codes: 0 ok, 1 fail, 2 quota abort.
process_region() {
  local rid="$1"
  local lease_id="${rid}-$$-${BASHPID:-$RANDOM}"
  local bbox cells gen_id rc dem_rc

  quota_abort_if_needed "before:${rid}" || return 2

  log_info "==== region ${rid} ===="
  set +e
  "${SCRIPT_DIR}/fetch-extracts.sh" --force "$rid"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    state_append "{\"event\":\"region_fail\",\"region_id\":\"${rid}\",\"reason\":\"fetch_rc_${rc}\"}"
    cleanup_region_scratch "$rid"
    return 1
  fi

  bbox="$(region_bbox_csv "$rid")"
  if [[ -n "$bbox" ]]; then
    set +e
    python3 "${SCRIPT_DIR}/prefetch-dem-bbox.py" \
      --elev-dir "$NAVI_ELEV_DIR" --bbox="$bbox" --lease-id="$lease_id"
    dem_rc=$?
    set -e
    if [[ $dem_rc -ne 0 ]]; then
      log_warn "DEM prefetch soft-fail region=${rid} rc=${dem_rc}"
    fi
  fi

  quota_abort_if_needed "pre_convert:${rid}" || {
    if [[ -n "$bbox" ]]; then
      python3 "${SCRIPT_DIR}/prefetch-dem-bbox.py" --elev-dir "$NAVI_ELEV_DIR" \
        --bbox="$bbox" --lease-release --lease-id="$lease_id" || true
    fi
    cleanup_region_scratch "$rid"
    return 2
  }

  set +e
  "${SCRIPT_DIR}/convert-region.sh" "$rid"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    state_append "{\"event\":\"region_fail\",\"region_id\":\"${rid}\",\"reason\":\"convert_rc_${rc}\"}"
    if [[ -n "$bbox" ]]; then
      python3 "${SCRIPT_DIR}/prefetch-dem-bbox.py" --elev-dir "$NAVI_ELEV_DIR" \
        --bbox="$bbox" --lease-release --lease-id="$lease_id" || true
      if [[ "${NAVI_SMOKE_EVICT_DEM:-1}" == "1" ]]; then
        python3 "${SCRIPT_DIR}/prefetch-dem-bbox.py" --elev-dir "$NAVI_ELEV_DIR" \
          --bbox="$bbox" --evict || true
      fi
    fi
    cleanup_region_scratch "$rid"
    return 1
  fi

  gen_id="$(generation_id "$rid")"
  set +e
  publish_single_region_safe "$rid" "$gen_id"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    state_append "{\"event\":\"region_fail\",\"region_id\":\"${rid}\",\"reason\":\"publish_validate_rc_${rc}\"}"
    if [[ -n "$bbox" ]]; then
      python3 "${SCRIPT_DIR}/prefetch-dem-bbox.py" --elev-dir "$NAVI_ELEV_DIR" \
        --bbox="$bbox" --lease-release --lease-id="$lease_id" || true
      if [[ "${NAVI_SMOKE_EVICT_DEM:-1}" == "1" ]]; then
        python3 "${SCRIPT_DIR}/prefetch-dem-bbox.py" --elev-dir "$NAVI_ELEV_DIR" \
          --bbox="$bbox" --evict || true
      fi
    fi
    cleanup_region_scratch "$rid"
    return 1
  fi

  cleanup_region_scratch "$rid"
  if [[ -n "$bbox" ]]; then
    python3 "${SCRIPT_DIR}/prefetch-dem-bbox.py" --elev-dir "$NAVI_ELEV_DIR" \
      --bbox="$bbox" --lease-release --lease-id="$lease_id" || true
    if [[ "${NAVI_SMOKE_EVICT_DEM:-1}" == "1" ]]; then
      python3 "${SCRIPT_DIR}/prefetch-dem-bbox.py" --elev-dir "$NAVI_ELEV_DIR" \
        --bbox="$bbox" --evict || true
    fi
  fi
  state_append "{\"event\":\"region_ok\",\"region_id\":\"${rid}\",\"generation\":\"${gen_id}\"}"
  log_info "region OK ${rid}"
  return 0
}

write_summary() {
  local status="${1:-running}"
  python3 - "$STATE_FILE" "$SUMMARY_FILE" "$status" "$START_EPOCH" <<'PY'
import json, sys, time
from pathlib import Path
state_path, summary_path, status, start = sys.argv[1:5]
start = int(start)
events = []
if Path(state_path).is_file():
    for line in Path(state_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
passed = [e for e in events if e.get("event") == "region_ok"]
failed = [e for e in events if e.get("event") == "region_fail"]
skipped = [e for e in events if e.get("event") == "region_skip"]
payload = {
    "status": status,
    "started_unix": start,
    "finished_unix": int(time.time()),
    "wall_seconds": int(time.time()) - start,
    "passed": len(passed),
    "failed": len(failed),
    "skipped": len(skipped),
    "failed_regions": failed,
    "skipped_regions": skipped,
    "last_events": events[-20:],
}
Path(summary_path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2))
PY
}

# --- prepare region lists ---
if [[ ! -f "$REGIONS_FILE" ]]; then
  log_info "generating Geofabrik leaf regions.conf"
  python3 "${SCRIPT_DIR}/gen-geofabrik-leaves.py" -o "$REGIONS_FILE"
fi

mapfile -t ALL_REGIONS < <(list_regions "$REGIONS_FILE" | awk -F'\t' '{print $1}')
log_info "parallel smoke regions=${#ALL_REGIONS[@]}"

if [[ -n "$ONLY" ]]; then
  IFS=',' read -r -a ONLY_ARR <<<"$ONLY"
  FILTERED=()
  for r in "${ALL_REGIONS[@]}"; do
    for o in "${ONLY_ARR[@]}"; do
      if [[ "$r" == "$o" || "$r" == *"${o}"* ]]; then
        FILTERED+=("$r")
        break
      fi
    done
  done
  ALL_REGIONS=("${FILTERED[@]}")
  [[ ${#ALL_REGIONS[@]} -gt 0 ]] || die "--only matched no regions"
fi

export NAVI_REGIONS_CONF="$REGIONS_FILE"
order_regions_array_follow_sun ALL_REGIONS "$REGIONS_FILE"
log_info "sun_order=${NAVI_BAKE_SUN_ORDER:-1} first_regions=$(printf '%s ' "${ALL_REGIONS[@]:0:5}")"

detect_concurrency
LARGE_CELLS="${NAVI_SMOKE_LARGE_DEM_CELLS:-150}"

log_info "concurrency detect: threads=${DETECTED_THREADS} mem_avail_gib=${DETECTED_MEM_GIB} reserve_cores=${DETECTED_RESERVE_CORES} reserve_ram_gib=${DETECTED_RESERVE_RAM_GIB} threads_per_worker=${DETECTED_THREADS_PER} rss_gib_per_worker=${DETECTED_RSS_PER} max_jobs_ceiling=${DETECTED_CEILING} by_cpu=${DETECTED_BY_CPU} by_ram=${DETECTED_BY_RAM} => jobs=${DETECTED_JOBS} large_dem_cells>${LARGE_CELLS} serial"

[[ "$RESUME" -eq 1 ]] || : >"$STATE_FILE"

POOL=()
SERIAL=()
for rid in "${ALL_REGIONS[@]}"; do
  if [[ "$RESUME" -eq 1 ]] && already_done "$rid"; then
    log_info "skip already published ${rid}"
    state_append "{\"event\":\"region_skip\",\"region_id\":\"${rid}\",\"reason\":\"already_published\"}"
    continue
  fi
  cells="$(region_dem_cells "$rid")"
  if [[ "$cells" -gt "$LARGE_CELLS" ]]; then
    SERIAL+=("$rid")
    log_info "route serial region=${rid} dem_cells=${cells} (>${LARGE_CELLS})"
  else
    POOL+=("$rid")
    log_info "route pool region=${rid} dem_cells=${cells}"
  fi
done

if [[ "$LIMIT" -gt 0 ]]; then
  # Prefer pool regions for dry-runs / limited tests.
  POOL=("${POOL[@]:0:$LIMIT}")
  SERIAL=()
  log_info "limit=${LIMIT} -> pool=${#POOL[@]} serial=0"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  log_info "DRY-RUN: would process pool=${#POOL[@]} serial=${#SERIAL[@]} with jobs=${DETECTED_JOBS}"
  printf 'pool: %s\n' "${POOL[*]:-}"
  printf 'serial: %s\n' "${SERIAL[*]:-}"
  write_summary dry_run
  exit 0
fi

WORKER_SLOT_DIR="$(mktemp -d "${REPORT_DIR}/slots.XXXXXX")"
trap 'rm -rf "$WORKER_SLOT_DIR"' EXIT

wait_for_slot() {
  while true; do
    local n
    n="$(find "$WORKER_SLOT_DIR" -type f 2>/dev/null | wc -l)"
    if [[ "$n" -lt "$DETECTED_JOBS" ]]; then
      return 0
    fi
    sleep 2
  done
}

take_slot() {
  local rid="$1"
  : >"${WORKER_SLOT_DIR}/${rid}.$$"
}

release_slot() {
  local rid="$1"
  rm -f "${WORKER_SLOT_DIR}/${rid}.$$"
}

QUOTA_HIT=0
done_count=0

# --- concurrent pool ---
pids=()
declare -A PID_TO_RID=()
for rid in "${POOL[@]}"; do
  if [[ "$QUOTA_HIT" -eq 1 ]]; then
    break
  fi
  if smoke_stop_requested; then
    log_info "stop requested (${STOP_FILE}); not dispatching further pool regions"
    state_append "{\"event\":\"smoke_stop\",\"reason\":\"stop_file\",\"phase\":\"pool\"}"
    break
  fi
  wait_for_slot
  if ! quota_abort_if_needed "dispatch:${rid}"; then
    QUOTA_HIT=1
    break
  fi
  take_slot "$rid"
  (
    set +e
    process_region "$rid"
    rc=$?
    release_slot "$rid"
    exit "$rc"
  ) &
  pid=$!
  pids+=("$pid")
  PID_TO_RID["$pid"]="$rid"
  log_info "dispatched pool region=${rid} pid=${pid} inflight=$(find "$WORKER_SLOT_DIR" -type f | wc -l)/${DETECTED_JOBS}"
done

for pid in "${pids[@]+"${pids[@]}"}"; do
  set +e
  wait "$pid"
  rc=$?
  set -e
  rid="${PID_TO_RID[$pid]:-unknown}"
  if [[ $rc -eq 0 ]]; then
    done_count=$((done_count + 1))
  elif [[ $rc -eq 2 ]]; then
    QUOTA_HIT=1
    log_fail "worker quota abort region=${rid}"
  else
    log_warn "worker failed region=${rid} rc=${rc}"
  fi
done

# --- serial large regions ---
if [[ "$QUOTA_HIT" -eq 0 ]]; then
  for rid in "${SERIAL[@]+"${SERIAL[@]}"}"; do
    if smoke_stop_requested; then
      log_info "stop requested (${STOP_FILE}); skipping remaining serial regions"
      state_append "{\"event\":\"smoke_stop\",\"reason\":\"stop_file\",\"phase\":\"serial\"}"
      break
    fi
    if ! quota_abort_if_needed "serial_dispatch:${rid}"; then
      QUOTA_HIT=1
      break
    fi
    set +e
    process_region "$rid"
    rc=$?
    set -e
    if [[ $rc -eq 0 ]]; then
      done_count=$((done_count + 1))
    elif [[ $rc -eq 2 ]]; then
      QUOTA_HIT=1
      break
    fi
  done
fi

if [[ "$QUOTA_HIT" -eq 1 ]]; then
  write_summary aborted
  exit 2
fi
write_summary completed
log_info "parallel smoke finished session_ok=${done_count} jobs=${DETECTED_JOBS}"
