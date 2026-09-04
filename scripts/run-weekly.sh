#!/usr/bin/env bash
# Weekly orchestrator — runs each pipeline step in order.
# Prototype: safe to invoke by hand for one region before enabling the timer.
#
# Usage:
#   ./run-weekly.sh                     # all regions in regions.conf
#   ./run-weekly.sh --region hedmark    # single-region smoke test
#   ./run-weekly.sh --region hedmark --skip-fetch
#   ./run-weekly.sh --region hedmark --skip-publish

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

FILTER_IDS=()
SKIP_FETCH=0
SKIP_CONVERT=0
SKIP_PUBLISH=0
SKIP_CLEANUP=0
FORCE_FETCH=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region) FILTER_IDS+=("$2"); shift 2 ;;
    --skip-fetch) SKIP_FETCH=1; shift ;;
    --skip-convert) SKIP_CONVERT=1; shift ;;
    --skip-publish) SKIP_PUBLISH=1; shift ;;
    --skip-cleanup) SKIP_CLEANUP=1; shift ;;
    --force-fetch) FORCE_FETCH=1; shift ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
done

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="${NAVI_LOG_DIR}/weekly-${RUN_ID}.log"
mkdir -p "$NAVI_LOG_DIR"

atomic_log_begin "$LOG_FILE"

log_info "==== navi pack bake start run_id=${RUN_ID} ===="
log_info "log=${LOG_FILE} (atomic: written as .partial until success)"
log_info "regions_conf=${NAVI_REGIONS_CONF} profiles=${NAVI_PROFILES} delta_h=${NAVI_BAKE_DELTA_H}"
log_info "sun_order=${NAVI_BAKE_SUN_ORDER:-1} (local-night terminator; see sun-order-regions.py)"
# Pin the midnight meridian for this run (fetch + convert share the same order).
export NAVI_BAKE_START_UNIX="${NAVI_BAKE_START_UNIX:-$(date -u +%s)}"
log_info "bake_start_unix=${NAVI_BAKE_START_UNIX}"

cleanup_on_fail() {
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    log_fail "navi pack bake FAILED run_id=${RUN_ID} exit=${rc} — see ${LOG_FILE}.partial"
    atomic_log_abort
  fi
}
trap cleanup_on_fail EXIT

region_args=()
if [[ ${#FILTER_IDS[@]} -gt 0 ]]; then
  for r in "${FILTER_IDS[@]}"; do
    region_args+=("$r")
  done
fi

# 0. Quota gate before downloading anything large.
"${SCRIPT_DIR}/check-disk-quota.sh"

# 1. Fetch
if [[ "$SKIP_FETCH" -eq 0 ]]; then
  fetch_args=()
  [[ "$FORCE_FETCH" -eq 1 ]] && fetch_args+=(--force)
  fetch_args+=("${region_args[@]+"${region_args[@]}"}")
  "${SCRIPT_DIR}/fetch-extracts.sh" "${fetch_args[@]}"
else
  log_info "skip fetch"
fi

# 2. Convert
if [[ "$SKIP_CONVERT" -eq 0 ]]; then
  if [[ ${#FILTER_IDS[@]} -gt 0 ]]; then
    for r in "${FILTER_IDS[@]}"; do
      "${SCRIPT_DIR}/convert-region.sh" "$r"
    done
  else
    "${SCRIPT_DIR}/convert-region.sh" --all
  fi
else
  log_info "skip convert"
fi

# 3. Publish (includes validate + optional town-route bake + blue-green swap)
if [[ "$SKIP_PUBLISH" -eq 0 ]]; then
  pub_args=()
  if [[ ${#FILTER_IDS[@]} -gt 0 ]]; then
    for r in "${FILTER_IDS[@]}"; do
      pub_args+=(--region "$r")
    done
  fi
  "${SCRIPT_DIR}/publish-packs.sh" "${pub_args[@]+"${pub_args[@]}"}"
else
  log_info "skip publish"
fi

# 4. Cleanup / disk report (self-maintaining scrub)
if [[ "$SKIP_CLEANUP" -eq 0 ]]; then
  "${SCRIPT_DIR}/cleanup.sh" --skip-quota-gate
else
  log_info "skip cleanup"
fi

trap - EXIT
log_info "==== navi pack bake OK run_id=${RUN_ID} ===="
log_info "FAILED marker absent — success. log=${LOG_FILE}"
atomic_log_commit
exit 0
