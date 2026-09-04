#!/usr/bin/env bash
# Graceful stop for planet smoke runners.
#
# Creates STOP_SMOKE under NAVI_PACK_ROOT. Runners that poll it exit between
# regions. For an already-running orchestrator that does not yet poll, this
# script waits for the in-flight navi-indexed-convert to exit, waits briefly
# for validate/publish/cleanup, then TERMs the orchestrator so the next region
# is not started mid-write.
#
# Usage:
#   ./stop-smoke.sh              # request stop; wait for clean boundary
#   ./stop-smoke.sh --force      # TERM convert + smoke immediately (last resort)
#   ./stop-smoke.sh --status

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

FORCE=0
STATUS_ONLY=0
# Alps-class converts can take hours; default wait is 6h before TERM convert.
CONVERT_WAIT_SEC="${NAVI_SMOKE_STOP_CONVERT_WAIT_SEC:-21600}"
POST_CONVERT_SEC="${NAVI_SMOKE_STOP_POST_CONVERT_SEC:-180}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --status) STATUS_ONLY=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

STOP_FILE="${NAVI_PACK_ROOT}/STOP_SMOKE"
STATE_FILE="${NAVI_LOG_DIR}/planet-smoke/state.jsonl"

list_smoke() {
  pgrep -af 'scripts/run-planet-smoke' 2>/dev/null | grep -v stop-smoke | grep -v 'extglob' || true
}

list_convert() {
  pgrep -af 'navi-indexed-convert' 2>/dev/null | grep -v 'extglob' || true
}

inflight_region() {
  # Best-effort: scrape convert --data-dir .../scratch/convert/<rid>
  local line
  line="$(pgrep -af 'navi-indexed-convert' 2>/dev/null | grep -v 'extglob' | head -1 || true)"
  if [[ "$line" =~ scratch/convert/([A-Za-z0-9_]+) ]]; then
    echo "${BASH_REMATCH[1]}"
  fi
}

if [[ "$STATUS_ONLY" -eq 1 ]]; then
  echo "stop_file=$([[ -f $STOP_FILE ]] && echo present || echo absent)"
  echo "inflight_region=$(inflight_region)"
  echo "smoke:"
  list_smoke || echo "  (none)"
  echo "convert:"
  list_convert || echo "  (none)"
  exit 0
fi

mkdir -p "$(dirname "$STOP_FILE")"
date -u +%Y-%m-%dT%H:%M:%SZ >"$STOP_FILE"
echo "wrote ${STOP_FILE}"

cleanup_markers() {
  echo "cleaning leftover markers / leases / partials / convert scratch…"
  find "${NAVI_GENERATIONS_DIR}" -name '.smoke_in_progress' -delete 2>/dev/null || true
  find "${NAVI_ELEV_DIR}" -name '*.partial' -delete 2>/dev/null || true
  find "${NAVI_ELEV_DIR}" -name '*.partial.*' -delete 2>/dev/null || true
  find "${NAVI_EXTRACTS_DIR}" -name '*.partial' -delete 2>/dev/null || true
  if [[ -d "${NAVI_ELEV_DIR}/.tile_leases" ]]; then
    find "${NAVI_ELEV_DIR}/.tile_leases" -type f -delete 2>/dev/null || true
    find "${NAVI_ELEV_DIR}/.tile_leases" -type d -empty -delete 2>/dev/null || true
  fi
  if [[ -d "${NAVI_CONVERT_DIR}" ]]; then
    find "${NAVI_CONVERT_DIR}" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} + 2>/dev/null || true
  fi
  if [[ -d "${NAVI_EXTRACTS_DIR}" ]]; then
    find "${NAVI_EXTRACTS_DIR}" -maxdepth 1 -type f \( -name '*.osm.pbf' -o -name '*.md5' -o -name '*.partial' \) -delete 2>/dev/null || true
  fi
}

if [[ "$FORCE" -eq 1 ]]; then
  echo "FORCE: sending TERM to convert + smoke"
  pkill -TERM -f 'navi-indexed-convert' 2>/dev/null || true
  pkill -TERM -f 'scripts/run-planet-smoke' 2>/dev/null || true
  sleep 2
  pkill -KILL -f 'navi-indexed-convert' 2>/dev/null || true
  pkill -KILL -f 'scripts/run-planet-smoke' 2>/dev/null || true
  cleanup_markers
  rm -f "$STOP_FILE"
  echo "force stop complete"
  exit 0
fi

RID="$(inflight_region)"
echo "in-flight convert region=${RID:-unknown}"
echo "waiting up to ${CONVERT_WAIT_SEC}s for navi-indexed-convert to exit…"
elapsed=0
while pgrep -f 'navi-indexed-convert' >/dev/null 2>&1; do
  if [[ "$elapsed" -ge "$CONVERT_WAIT_SEC" ]]; then
    echo "convert still running after ${CONVERT_WAIT_SEC}s — sending TERM to convert" >&2
    pkill -TERM -f 'navi-indexed-convert' 2>/dev/null || true
    sleep 10
    break
  fi
  sleep 5
  elapsed=$((elapsed + 5))
  if [[ $((elapsed % 60)) -eq 0 ]]; then
    echo "  still converting… ${elapsed}s elapsed region=${RID:-unknown}"
  fi
done
echo "convert gone (waited ${elapsed}s)"

# Allow validate + publish + per-region cleanup for the finished region.
if [[ -n "$RID" && -f "$STATE_FILE" ]]; then
  echo "waiting up to ${POST_CONVERT_SEC}s for state event on ${RID}…"
  post=0
  while [[ "$post" -lt "$POST_CONVERT_SEC" ]]; do
    if grep -q "\"region_id\":\"${RID}\"" "$STATE_FILE" 2>/dev/null \
      && grep -E "\"region_id\":\"${RID}\".*\"(region_ok|region_fail|event\":\"region_)" "$STATE_FILE" 2>/dev/null; then
      # Prefer an explicit ok/fail line for this rid near end of file.
      if awk -v id="$RID" '
          $0 ~ "\"region_id\":\"" id "\"" && ($0 ~ "region_ok" || $0 ~ "region_fail") { found=1 }
          END { exit found ? 0 : 1 }
        ' "$STATE_FILE"; then
        echo "saw terminal state for ${RID}"
        break
      fi
    fi
    # If smoke already started another convert, stop waiting to publish.
    if pgrep -f 'navi-indexed-convert' >/dev/null 2>&1; then
      echo "new convert started; stopping orchestrator now"
      break
    fi
    sleep 2
    post=$((post + 2))
  done
else
  sleep "$POST_CONVERT_SEC"
fi

echo "sending TERM to smoke orchestrators"
pkill -TERM -f 'scripts/run-planet-smoke' 2>/dev/null || true
sleep 3
pkill -KILL -f 'scripts/run-planet-smoke' 2>/dev/null || true
# If a new convert slipped in, stop it too (scratch only; published untouched).
pkill -TERM -f 'navi-indexed-convert' 2>/dev/null || true
sleep 2
pkill -KILL -f 'navi-indexed-convert' 2>/dev/null || true

echo "remaining processes:"
list_smoke || echo "  smoke: none"
list_convert || echo "  convert: none"

cleanup_markers
rm -f "$STOP_FILE"
echo "stop complete"
