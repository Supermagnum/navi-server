#!/usr/bin/env bash
# Probe Geofabrik download health before resuming a paused bake.
#
# Requires THREE consecutive clean rounds (all probe URLs HTTP 200 with
# stable Content-Length) spaced INTERVAL_SECS apart, then exits 0 and
# prints a go/no-go recommendation. Does NOT resume any bake.
#
# Usage:
#   ./probe-geofabrik-health.sh
#   ./probe-geofabrik-health.sh --interval 300
#   ./probe-geofabrik-health.sh --once          # single round (exit 1 if unclean)
#
# Logs: ${NAVI_LOG_DIR:-data/logs}/geofabrik-health-probe.log

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

INTERVAL_SECS=300
NEED_CLEAN=3
ONCE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --interval) INTERVAL_SECS="$2"; shift 2 ;;
    --need) NEED_CLEAN="$2"; shift 2 ;;
    --once) ONCE=1; shift ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
done

LOG_FILE="${NAVI_LOG_DIR}/geofabrik-health-probe.log"
mkdir -p "$NAVI_LOG_DIR"
STATE_DIR="${NAVI_STATE_DIR}/geofabrik-health"
mkdir -p "$STATE_DIR"
LENGTHS_FILE="${STATE_DIR}/content-lengths.env"

# Greenland (paused fetch) + Iceland (also 502 at last check) + a not-yet-fetched
# batch-1 leaf (Brazil Sul) as a third large object.
PROBE_URLS=(
  "https://download.geofabrik.de/north-america/greenland-latest.osm.pbf"
  "https://download.geofabrik.de/europe/iceland-latest.osm.pbf"
  "https://download.geofabrik.de/south-america/brazil/sul-latest.osm.pbf"
)

probe_log() {
  local msg="$1"
  local ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf '%s %s\n' "$ts" "$msg" | tee -a "$LOG_FILE"
}

# HEAD first; if Content-Length missing, fall back to a 1-byte ranged GET
# (some edges omit length on HEAD). Returns via globals: HTTP_CODE LEN_HDR.
probe_one() {
  local url="$1"
  local headers tmp
  headers="$(mktemp)"
  tmp="$(mktemp)"
  HTTP_CODE=""
  LEN_HDR=""

  set +e
  HTTP_CODE="$(curl -sS -o /dev/null -D "$headers" -w '%{http_code}' \
    --connect-timeout 30 --max-time 90 -L --head "$url" 2>/dev/null)"
  local rc=$?
  set -e
  if [[ $rc -ne 0 || -z "$HTTP_CODE" ]]; then
    HTTP_CODE="000"
  fi
  LEN_HDR="$(awk 'BEGIN{IGNORECASE=1} /^content-length:/{gsub(/\r/,""); print $2; exit}' "$headers" || true)"

  if [[ -z "$LEN_HDR" || "$LEN_HDR" == "0" ]]; then
    set +e
    HTTP_CODE="$(curl -sS -o "$tmp" -D "$headers" -w '%{http_code}' \
      --connect-timeout 30 --max-time 120 -L -r 0-0 "$url" 2>/dev/null)"
    rc=$?
    set -e
    if [[ $rc -ne 0 || -z "$HTTP_CODE" ]]; then
      HTTP_CODE="000"
    fi
    LEN_HDR="$(awk 'BEGIN{IGNORECASE=1}
      /^content-range:/{
        gsub(/\r/,"")
        if (match($0, /\/([0-9]+)$/)) print substr($0, RSTART+1, RLENGTH-1)
        exit
      }
      /^content-length:/{gsub(/\r/,""); print $2; exit}' "$headers" || true)"
  fi

  rm -f "$headers" "$tmp"
}

load_expected_lengths() {
  EXPECTED=()
  local i
  for i in "${!PROBE_URLS[@]}"; do
    EXPECTED[$i]=""
  done
  if [[ -f "$LENGTHS_FILE" ]]; then
    local line key val
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ -z "$line" || "$line" == \#* ]] && continue
      key="${line%%=*}"
      val="${line#*=}"
      case "$key" in
        EXPECTED_*)
          i="${key#EXPECTED_}"
          EXPECTED[$i]="$val"
          ;;
      esac
    done <"$LENGTHS_FILE"
  fi
}

save_expected_lengths() {
  local i
  : >"$LENGTHS_FILE"
  for i in "${!PROBE_URLS[@]}"; do
    printf 'EXPECTED_%s=%s\n' "$i" "${EXPECTED[$i]:-}" >>"$LENGTHS_FILE"
  done
}

run_round() {
  local round="$1"
  local all_ok=1
  local i url code len key expected
  probe_log "---- round=${round} begin ----"
  load_expected_lengths

  for i in "${!PROBE_URLS[@]}"; do
    url="${PROBE_URLS[$i]}"
    probe_one "$url"
    code="$HTTP_CODE"
    len="${LEN_HDR:-}"
    expected="${EXPECTED[$i]:-}"

    if [[ "$code" != "200" ]]; then
      all_ok=0
      probe_log "FAIL url=${url} http=${code} content_length=${len:-none}"
      continue
    fi
    if [[ -z "$len" || "$len" == "0" ]]; then
      all_ok=0
      probe_log "FAIL url=${url} http=${code} content_length=missing"
      continue
    fi
    if [[ -n "$expected" && "$expected" != "$len" ]]; then
      all_ok=0
      probe_log "FAIL url=${url} http=${code} content_length=${len} expected=${expected} (size changed/flapped)"
      # Reset baseline so a genuine Geofabrik re-publish can re-stabilize.
      EXPECTED[$i]="$len"
      continue
    fi
    if [[ -z "$expected" ]]; then
      EXPECTED[$i]="$len"
      probe_log "OK   url=${url} http=${code} content_length=${len} (baseline set)"
    else
      probe_log "OK   url=${url} http=${code} content_length=${len} (matches baseline)"
    fi
  done

  save_expected_lengths

  if [[ "$all_ok" -eq 1 ]]; then
    probe_log "ROUND_CLEAN round=${round}"
    return 0
  fi
  probe_log "ROUND_DIRTY round=${round}"
  return 1
}

probe_log "probe start interval_secs=${INTERVAL_SECS} need_clean=${NEED_CLEAN} once=${ONCE} log=${LOG_FILE}"
probe_log "urls: ${PROBE_URLS[*]}"

clean_streak=0
round=0
while true; do
  round=$((round + 1))
  if run_round "$round"; then
    clean_streak=$((clean_streak + 1))
    probe_log "clean_streak=${clean_streak}/${NEED_CLEAN}"
  else
    if [[ "$clean_streak" -gt 0 ]]; then
      probe_log "clean_streak reset (was ${clean_streak})"
    fi
    clean_streak=0
  fi

  if [[ "$ONCE" -eq 1 ]]; then
    if [[ "$clean_streak" -ge 1 ]]; then
      probe_log "ONCE mode: single round clean — not sufficient for resume (need ${NEED_CLEAN} consecutive)"
      exit 0
    fi
    probe_log "ONCE mode: single round dirty"
    exit 1
  fi

  if [[ "$clean_streak" -ge "$NEED_CLEAN" ]]; then
    probe_log "STABLE: ${NEED_CLEAN} consecutive clean rounds at utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    probe_log "RECOMMENDATION: go — Geofabrik looks stable; resume bake only with explicit human go-ahead"
    printf '\nSTABLE — %s consecutive clean checks passed.\n' "$NEED_CLEAN"
    printf 'Recommendation: GO (resume only after you explicitly decide).\n'
    printf 'Log: %s\n' "$LOG_FILE"
    exit 0
  fi

  probe_log "sleep ${INTERVAL_SECS}s until next round"
  sleep "$INTERVAL_SECS"
done
