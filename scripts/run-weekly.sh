#!/usr/bin/env bash
# Weekly orchestrator — runs each pipeline step in order.
# Prototype: safe to invoke by hand for one region before enabling the timer.
#
# Fetch prefers Geofabrik .osc.gz incremental updates for every region in
# NAVI_REGIONS_CONF (fetch-extracts.sh --prefer-incremental). Exit 10 from
# fetch-incremental.sh falls through to the existing full-fetch path.
# --force-fetch skips incremental and always full-fetches.
#
# End-of-run cleanup uses --no-extracts (no age-delete of extracts). Held-PBF
# total size is still capped by NAVI_HELD_PBF_BUDGET_GIB / NAVI_HELD_PBF_MAX_MIB
# so the 512 GB VPS envelope stays workable; oversize / over-budget extracts
# are dropped and those regions full-fetch next week.
#
# Convert concurrency: navi-indexed-convert uses pack-convert-core WorkerPoolPlan
# (autodetect cores + MemAvailable). This orchestrator converts regions
# sequentially and does not set NAVI_TILE_BUILD_CONCURRENCY or any second
# job-count mechanism — suitable for the small-VPS profile (8c / 16 GiB).
#
# Usage:
#   ./run-weekly.sh                     # all regions in regions.conf
#   ./run-weekly.sh --region hedmark    # single-region smoke test
#   ./run-weekly.sh --region hedmark --skip-fetch
#   ./run-weekly.sh --region hedmark --skip-publish
#   ./run-weekly.sh --force-fetch       # full fetch; skip incremental

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
      sed -n '2,26p' "$0"
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

# Resource profile (informational). Convert sizing is owned solely by
# WorkerPoolPlan inside navi-indexed-convert — do not set job counts here.
_host_cores="$(nproc 2>/dev/null || echo unknown)"
_host_mem_gib="$(awk '/MemAvailable:/ {printf "%.1f", $2/1024/1024; found=1} END{if(!found) print "unknown"}' /proc/meminfo 2>/dev/null || echo unknown)"
log_info "resource_profile host_cores=${_host_cores} mem_available_gib=${_host_mem_gib} convert=WorkerPoolPlan regions=sequential"
log_info "resource_profile weekly does not set NAVI_TILE_BUILD_CONCURRENCY (leave unset for autodetection)"
if [[ -n "${NAVI_TILE_BUILD_CONCURRENCY:-}" ]]; then
  log_warn "NAVI_TILE_BUILD_CONCURRENCY=${NAVI_TILE_BUILD_CONCURRENCY} is already set in the environment (not by weekly) — tile concurrency is pinned; unset for WorkerPoolPlan autodetection on small VPS"
fi
unset _host_cores _host_mem_gib

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

# 1. Fetch — prefer incremental for all regions; --force-fetch skips it.
if [[ "$SKIP_FETCH" -eq 0 ]]; then
  fetch_args=()
  if [[ "$FORCE_FETCH" -eq 1 ]]; then
    fetch_args+=(--force)
  else
    fetch_args+=(--prefer-incremental)
  fi
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

# 4. Cleanup / disk report (self-maintaining scrub).
# Skip age-based extract prune (--no-extracts) so held PBFs survive for
# Geofabrik incremental. cleanup.sh still enforces NAVI_HELD_PBF_BUDGET_GIB /
# NAVI_HELD_PBF_MAX_MIB (drop oversize / over-budget largest-first); regions
# without a retained held PBF fall back to full fetch on the next weekly.
if [[ "$SKIP_CLEANUP" -eq 0 ]]; then
  "${SCRIPT_DIR}/cleanup.sh" --skip-quota-gate --no-extracts
else
  log_info "skip cleanup"
fi

trap - EXIT
log_info "==== navi pack bake OK run_id=${RUN_ID} ===="
log_info "FAILED marker absent — success. log=${LOG_FILE}"
atomic_log_commit
exit 0
