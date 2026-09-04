#!/usr/bin/env bash
# ZFS quota monitor for the Navi pack dataset.
# Fails loudly (exit 1) when used space crosses NAVI_QUOTA_FAIL_PCT of quota.
# There was no pre-existing zfs-quota-monitor.sh on this box; this is the
# pipeline-local implementation (extend in place if a host-wide monitor appears).
#
# Usage:
#   ./check-disk-quota.sh
#   ./check-disk-quota.sh --report-only

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config
require_cmd zfs awk

REPORT_ONLY=0
[[ "${1:-}" == "--report-only" ]] && REPORT_ONLY=1

DATASET="${NAVI_ZFS_DATASET}"

if ! zfs list -H -o name "$DATASET" >/dev/null 2>&1; then
  # Fall back to filesystem capacity if not a ZFS dataset name we can see.
  log_warn "zfs dataset ${DATASET} not visible; falling back to df on ${NAVI_PACK_ROOT}"
  df -h "$NAVI_PACK_ROOT"
  used_pct="$(df -P "$NAVI_PACK_ROOT" | awk 'NR==2{gsub(/%/,"",$5); print $5}')"
  log_info "filesystem used_pct=${used_pct} warn=${NAVI_QUOTA_WARN_PCT} fail=${NAVI_QUOTA_FAIL_PCT}"
  if [[ "$REPORT_ONLY" -eq 1 ]]; then
    exit 0
  fi
  if awk -v u="$used_pct" -v f="$NAVI_QUOTA_FAIL_PCT" 'BEGIN{exit !(u+0 >= f+0)}'; then
    die "DISK QUOTA FAIL: filesystem ${used_pct}% full (>= ${NAVI_QUOTA_FAIL_PCT}%) — refusing to continue"
  fi
  if awk -v u="$used_pct" -v w="$NAVI_QUOTA_WARN_PCT" 'BEGIN{exit !(u+0 >= w+0)}'; then
    log_warn "DISK QUOTA WARN: filesystem ${used_pct}% full (>= ${NAVI_QUOTA_WARN_PCT}%)"
  fi
  exit 0
fi

# used / quota / available in bytes (one property per call — portable across zfs versions).
used="$(zfs get -Hp -o value used "$DATASET")"
quota="$(zfs get -Hp -o value quota "$DATASET")"
avail="$(zfs get -Hp -o value available "$DATASET")"

if [[ "$quota" == "0" || "$quota" == "none" ]]; then
  # No hard quota: use used/(used+avail) as effective fill.
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
  die "DISK QUOTA FAIL: ${DATASET} at ${pct}% (>= ${NAVI_QUOTA_FAIL_PCT}%) — refusing to continue"
fi
if awk -v u="$pct" -v w="$NAVI_QUOTA_WARN_PCT" 'BEGIN{exit !(u+0 >= w+0)}'; then
  log_warn "DISK QUOTA WARN: ${DATASET} at ${pct}% (>= ${NAVI_QUOTA_WARN_PCT}%)"
fi
