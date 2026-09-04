#!/usr/bin/env bash
# Cleanup: prune old scratch extracts (optional), old pack generations beyond
# blue-green rollback needs, and report disk usage vs ZFS quota.
#
# Usage:
#   ./cleanup.sh
#   ./cleanup.sh --prune-extracts   # also delete scratch PBFs older than keep window
#   ./cleanup.sh --keep 2

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

PRUNE_EXTRACTS=0
KEEP="${NAVI_KEEP_GENERATIONS}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prune-extracts) PRUNE_EXTRACTS=1; shift ;;
    --keep) KEEP="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
done

[[ "$KEEP" -ge 2 ]] || die "NAVI_KEEP_GENERATIONS/keep must be >= 2 (live + previous)"

# Always run quota check; fail loudly near full.
"${SCRIPT_DIR}/check-disk-quota.sh"

# Resolve protected generation paths.
protect=()
for link in "$NAVI_LIVE_LINK" "$NAVI_PREVIOUS_LINK"; do
  if [[ -L "$link" || -d "$link" ]]; then
    protect+=("$(readlink -f "$link")")
  fi
done

mapfile -t gens < <(find "$NAVI_GENERATIONS_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -nr | awk '{print $2}')

log_info "generations present=${#gens[@]} keep=${KEEP}"

idx=0
for g in "${gens[@]}"; do
  idx=$((idx + 1))
  skip=0
  for p in "${protect[@]}"; do
    [[ "$g" == "$p" ]] && skip=1
  done
  if [[ "$idx" -le "$KEEP" || "$skip" -eq 1 ]]; then
    log_info "keep ${g}"
    continue
  fi
  log_info "prune generation ${g}"
  rm -rf "$g"
  # Drop matching public tree entries (HTTP root only holds published packs).
  gen_name="$(basename "$g")"
  if [[ -d "${NAVI_PUBLISHED_DIR}/packs" ]]; then
    find "${NAVI_PUBLISHED_DIR}/packs" -mindepth 2 -maxdepth 2 -type d -name "$gen_name" -print | while read -r pd; do
      log_info "prune published ${pd}"
      rm -rf "$pd"
    done
  fi
done

# Staging leftovers from crashed runs (>2 days).
if [[ -d "$NAVI_STAGING_DIR" ]]; then
  find "$NAVI_STAGING_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +2 -print | while read -r d; do
    log_info "prune stale staging ${d}"
    rm -rf "$d"
  done
fi

# Never leave publish staging crumbs under the HTTP root.
if [[ -d "${NAVI_PUBLISHED_DIR}/.staging" ]]; then
  rm -rf "${NAVI_PUBLISHED_DIR}/.staging"
fi

# Convert scratch: keep last convert for debugging unless pruning extracts too.
if [[ "$PRUNE_EXTRACTS" -eq 1 ]]; then
  log_info "pruning convert scratch under ${NAVI_CONVERT_DIR}"
  find "$NAVI_CONVERT_DIR" -mindepth 1 -maxdepth 1 -mtime +7 -exec rm -rf {} +
  log_info "pruning extracts older than 21 days under ${NAVI_EXTRACTS_DIR}"
  find "$NAVI_EXTRACTS_DIR" -type f -name '*.osm.pbf' -mtime +21 -print -delete
  find "$NAVI_EXTRACTS_DIR" -type f -name '*.md5' -mtime +21 -print -delete
fi

# Log disk usage summary.
log_info "disk usage:"
du -sh "$NAVI_PACK_ROOT"/* 2>/dev/null || true
"${SCRIPT_DIR}/check-disk-quota.sh" --report-only

log_info "cleanup complete"
