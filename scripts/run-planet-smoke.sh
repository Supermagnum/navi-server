#!/usr/bin/env bash
# Planet-coverage smoke test (Geofabrik leaf extracts = world coverage).
#
# Deliberately does NOT download planet-latest.osm.pbf (~88 GiB). Holding the
# planet alongside convert scratch + growing published packs would blow past
# the 250G Mypool/navi quota. Leaf extracts give the same geographic coverage
# with per-region cleanup (fetch → DEM prefetch → convert → validate →
# publish → delete PBF+convert scratch).
#
# - Single publish tree (no blue-green / previous retention)
# - No town-route bake
# - Δh on (NAVI_BAKE_DELTA_H=1 + DEM prefetch per region bbox)
# - Quota abort at NAVI_SMOKE_QUOTA_ABORT_PCT (default 90)
# - Does NOT enable the weekly timer
#
# Usage:
#   ./run-planet-smoke.sh
#   ./run-planet-smoke.sh --resume
#   ./run-planet-smoke.sh --limit 5          # debug
#   ./run-planet-smoke.sh --only us_west_virginia,europe_norway_sorlandet

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
ABORT_PCT="${NAVI_SMOKE_QUOTA_ABORT_PCT:-90}"
REPORT_DIR="${NAVI_LOG_DIR}/planet-smoke"
STATE_FILE="${REPORT_DIR}/state.jsonl"
SUMMARY_FILE="${REPORT_DIR}/summary.json"
REGIONS_FILE="${NAVI_PACK_ROOT}/regions.planet.conf"
BBOX_FILE="${REGIONS_FILE}.bboxes.json"
STOP_FILE="${NAVI_PACK_ROOT}/STOP_SMOKE"
START_EPOCH="$(date -u +%s)"

smoke_stop_requested() {
  [[ -f "$STOP_FILE" ]]
}

mkdir -p "$REPORT_DIR" "${NAVI_PUBLISHED_DIR}/packs"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --resume) RESUME=1; shift ;;
    --limit) LIMIT="$2"; shift 2 ;;
    --only) ONLY="$2"; shift 2 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
done

export NAVI_BAKE_DELTA_H=1
export NAVI_BAKE_TOWN_ROUTES=0
export NAVI_REGIONS_CONF="$REGIONS_FILE"
: "${NAVI_ELEV_DIR:=${NAVI_PACK_ROOT}/elevation}"
export NAVI_ELEV_DIR
# Re-load so child scripts inherit overrides via common.sh preserve logic.
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
    echo "{\"event\":\"quota_abort\",\"phase\":\"${phase}\",\"used_pct\":${pct}}" >>"$STATE_FILE"
    write_summary aborted
    exit 2
  fi
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
        line=line.strip()
        if line:
            events.append(json.loads(line))
passed = [e for e in events if e.get("event")=="region_ok"]
failed = [e for e in events if e.get("event")=="region_fail"]
skipped = [e for e in events if e.get("event")=="region_skip"]
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

already_done() {
  region_is_published "$1"
}

publish_single_region() {
  publish_single_region_safe "$@"
}

cleanup_region_scratch() {
  local rid="$1"
  local pbf
  pbf="$(region_pbf_path "$rid")"
  rm -f "$pbf" "${pbf}.md5" "${pbf}.partial"
  rm -rf "${NAVI_CONVERT_DIR}/${rid}"
  rm -rf "${NAVI_STAGING_DIR:?}/"* 2>/dev/null || true
  log_info "cleaned scratch for ${rid}"
}

# --- prepare region list ---
if [[ ! -f "$REGIONS_FILE" ]]; then
  log_info "generating Geofabrik leaf regions.conf"
  python3 "${SCRIPT_DIR}/gen-geofabrik-leaves.py" -o "$REGIONS_FILE"
fi

mapfile -t ALL_REGIONS < <(list_regions "$REGIONS_FILE" | awk -F'\t' '{print $1}')
log_info "planet smoke regions=${#ALL_REGIONS[@]} elev_dir=${NAVI_ELEV_DIR} abort_pct=${ABORT_PCT}"

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
  [[ ${#ALL_REGIONS[@]} -gt 0 ]] || die "--only matched no regions (try north_america_us_west_virginia)"
fi

# Follow-the-sun order (local-night terminator). Disable with NAVI_BAKE_SUN_ORDER=0.
export NAVI_REGIONS_CONF="$REGIONS_FILE"
order_regions_array_follow_sun ALL_REGIONS "$REGIONS_FILE"
log_info "sun_order=${NAVI_BAKE_SUN_ORDER:-1} first_regions=$(printf '%s ' "${ALL_REGIONS[@]:0:5}")"

done_count=0
[[ "$RESUME" -eq 1 ]] || : >"$STATE_FILE"

for rid in "${ALL_REGIONS[@]}"; do
  if smoke_stop_requested; then
    log_info "stop requested (${STOP_FILE}); finishing after previous region"
    echo "{\"event\":\"smoke_stop\",\"reason\":\"stop_file\"}" >>"$STATE_FILE"
    break
  fi

  if [[ "$LIMIT" -gt 0 && "$done_count" -ge "$LIMIT" ]]; then
    log_info "limit ${LIMIT} reached"
    break
  fi

  if [[ "$RESUME" -eq 1 ]] && already_done "$rid"; then
    log_info "skip already published ${rid}"
    echo "{\"event\":\"region_skip\",\"region_id\":\"${rid}\",\"reason\":\"already_published\"}" >>"$STATE_FILE"
    continue
  fi

  quota_abort_if_needed "before:${rid}"

  # Do not early-exit awk: with pipefail that SIGPIPEs list_regions on the
  # first alphabetically matched region and aborts the whole smoke (exit 141).
  src_line="$(list_regions "$REGIONS_FILE" | awk -F'\t' -v id="$rid" '$1==id{print $2}')"
  [[ -n "$src_line" ]] || { echo "{\"event\":\"region_fail\",\"region_id\":\"${rid}\",\"reason\":\"missing_source\"}" >>"$STATE_FILE"; continue; }

  log_info "==== region ${rid} ===="
  set +e
  "${SCRIPT_DIR}/fetch-extracts.sh" --force "$rid"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    echo "{\"event\":\"region_fail\",\"region_id\":\"${rid}\",\"reason\":\"fetch_rc_${rc}\"}" >>"$STATE_FILE"
    cleanup_region_scratch "$rid"
    continue
  fi

  # DEM prefetch from bbox file when available
  if [[ -f "$BBOX_FILE" ]]; then
    bbox="$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); b=d.get(sys.argv[2]); print(','.join(map(str,b)) if b else '')" "$BBOX_FILE" "$rid")"
    if [[ -n "$bbox" ]]; then
      set +e
      # Use --bbox=VALUE so negative latitudes are not parsed as flags.
      python3 "${SCRIPT_DIR}/prefetch-dem-bbox.py" --elev-dir "$NAVI_ELEV_DIR" --bbox="$bbox"
      dem_rc=$?
      set -e
      if [[ $dem_rc -ne 0 ]]; then
        log_warn "DEM prefetch soft-fail region=${rid} rc=${dem_rc} (convert may bake zero Δh for missing tiles)"
      fi
    fi
  fi

  quota_abort_if_needed "pre_convert:${rid}"

  set +e
  "${SCRIPT_DIR}/convert-region.sh" "$rid"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    echo "{\"event\":\"region_fail\",\"region_id\":\"${rid}\",\"reason\":\"convert_rc_${rc}\"}" >>"$STATE_FILE"
    cleanup_region_scratch "$rid"
    continue
  fi

  gen_id="$(generation_id "$rid")"
  set +e
  publish_single_region "$rid" "$gen_id"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    echo "{\"event\":\"region_fail\",\"region_id\":\"${rid}\",\"reason\":\"publish_validate_rc_${rc}\"}" >>"$STATE_FILE"
    cleanup_region_scratch "$rid"
    continue
  fi

  cleanup_region_scratch "$rid"
  # Evict this region's DEM tiles after publish so the global elev cache does
  # not accumulate toward multi-hundred-GiB (tiles are global by id; overlap
  # will re-fetch — cheap vs quota abort).
  if [[ -f "$BBOX_FILE" ]]; then
    bbox="$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); b=d.get(sys.argv[2]); print(','.join(map(str,b)) if b else '')" "$BBOX_FILE" "$rid")"
    if [[ -n "$bbox" && "${NAVI_SMOKE_EVICT_DEM:-1}" == "1" ]]; then
      python3 "${SCRIPT_DIR}/prefetch-dem-bbox.py" --elev-dir "$NAVI_ELEV_DIR" --bbox="$bbox" --evict || true
    fi
  fi
  echo "{\"event\":\"region_ok\",\"region_id\":\"${rid}\",\"generation\":\"${gen_id}\"}" >>"$STATE_FILE"
  done_count=$((done_count + 1))
  log_info "region OK ${rid} (${done_count} this session)"
done

write_summary completed
log_info "planet smoke finished session_ok=${done_count}"
