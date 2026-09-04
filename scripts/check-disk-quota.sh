#!/usr/bin/env bash
# Disk capacity gate for the pack data root.
# Prefer an optional ZFS dataset (NAVI_ZFS_DATASET) when present; otherwise use
# plain filesystem fill from `df` on NAVI_PACK_ROOT. Disk space is what matters —
# ZFS is optional, not required.
#
# Usage:
#   ./check-disk-quota.sh
#   ./check-disk-quota.sh --report-only

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config
require_cmd awk

REPORT_ONLY=0
[[ "${1:-}" == "--report-only" ]] && REPORT_ONLY=1

DATASET="${NAVI_ZFS_DATASET:-}"

check_df() {
  local root="$1"
  df -h "$root"
  local used_pct
  used_pct="$(df -P "$root" | awk 'NR==2{gsub(/%/,"",$5); print $5}')"
  log_info "filesystem ${root} used_pct=${used_pct}% warn=${NAVI_QUOTA_WARN_PCT} fail=${NAVI_QUOTA_FAIL_PCT}"
  if [[ "$REPORT_ONLY" -eq 1 ]]; then
    return 0
  fi
  if awk -v u="$used_pct" -v f="$NAVI_QUOTA_FAIL_PCT" 'BEGIN{exit !(u+0 >= f+0)}'; then
    die "DISK SPACE FAIL: filesystem ${used_pct}% full (>= ${NAVI_QUOTA_FAIL_PCT}%) — refusing to continue"
  fi
  if awk -v u="$used_pct" -v w="$NAVI_QUOTA_WARN_PCT" 'BEGIN{exit !(u+0 >= w+0)}'; then
    log_warn "DISK SPACE WARN: filesystem ${used_pct}% full (>= ${NAVI_QUOTA_WARN_PCT}%)"
  fi
}

if [[ -z "$DATASET" ]] || ! command -v zfs >/dev/null 2>&1; then
  if [[ -n "$DATASET" ]]; then
    log_warn "zfs tool not installed; using df on ${NAVI_PACK_ROOT}"
  fi
  check_df "$NAVI_PACK_ROOT"
  exit 0
fi

if ! zfs list -H -o name "$DATASET" >/dev/null 2>&1; then
  log_warn "zfs dataset ${DATASET} not visible; falling back to df on ${NAVI_PACK_ROOT}"
  check_df "$NAVI_PACK_ROOT"
  exit 0
fi

# used / quota / available in bytes (one property per call — portable across zfs versions).
used="$(zfs get -Hp -o value used "$DATASET")"
quota="$(zfs get -Hp -o value quota "$DATASET")"
avail="$(zfs get -Hp -o value available "$DATASET")"

if [[ "$quota" == "0" || "$quota" == "none" ]]; then
  total=$((used + avail))
  if [[ "$total" -le 0 ]]; then
    die "cannot compute capacity for ${DATASET}"
  fi
  pct="$(awk -v u="$used" -v t="$total" 'BEGIN{printf "%.1f", (u*100)/t}')"
  log_info "zfs ${DATASET} used=$(bytes_to_mib "$used")MiB avail=$(bytes_to_mib "$avail")MiB fill=${pct}% (no quota property)"
else
  pct="$(awk -v u="$used" -v q="$quota" 'BEGIN{printf "%.1f", (u*100)/q}')"
  log_info "zfs ${DATASET} used=$(bytes_to_mib "$used")MiB quota=$(bytes_to_mib "$quota")MiB used_pct=${pct}% warn=${NAVI_QUOTA_WARN_PCT} fail=${NAVI_QUOTA_FAIL_PCT}"
fi

if [[ "$REPORT_ONLY" -eq 1 ]]; then
  exit 0
fi

if awk -v u="$pct" -v f="$NAVI_QUOTA_FAIL_PCT" 'BEGIN{exit !(u+0 >= f+0)}'; then
  die "DISK SPACE FAIL: ${DATASET} at ${pct}% (>= ${NAVI_QUOTA_FAIL_PCT}%) — refusing to continue"
fi
if awk -v u="$pct" -v w="$NAVI_QUOTA_WARN_PCT" 'BEGIN{exit !(u+0 >= w+0)}'; then
  log_warn "DISK SPACE WARN: ${DATASET} at ${pct}% (>= ${NAVI_QUOTA_WARN_PCT}%)"
fi
