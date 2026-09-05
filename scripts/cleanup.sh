#!/usr/bin/env bash
# Self-maintaining scrub: prune outdated generations, scratch, extracts, logs,
# locks, and partial files; then report disk space (filesystem or optional ZFS).
#
# Usage:
#   ./cleanup.sh                 # full scrub (safe defaults from config.env)
#   ./cleanup.sh --keep 2
#   ./cleanup.sh --no-extracts   # keep PBF extracts (still prunes convert/logs)
#   ./cleanup.sh --report-only   # disk report only, no deletes
#
# Installed as a daily systemd timer by setup-server.sh --apply-service
# (navi-pack-scrub.timer). Also runs at the end of each weekly bake.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

PRUNE_EXTRACTS=1
KEEP="${NAVI_KEEP_GENERATIONS}"
REPORT_ONLY=0
SKIP_QUOTA_GATE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prune-extracts) PRUNE_EXTRACTS=1; shift ;; # legacy alias (default on)
    --no-extracts) PRUNE_EXTRACTS=0; shift ;;
    --keep) KEEP="$2"; shift 2 ;;
    --report-only) REPORT_ONLY=1; shift ;;
    --skip-quota-gate) SKIP_QUOTA_GATE=1; shift ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
done

[[ "$KEEP" -ge 2 ]] || die "NAVI_KEEP_GENERATIONS/keep must be >= 2 (live + previous)"

LOG_KEEP_DAYS="${NAVI_LOG_KEEP_DAYS:-14}"
CONVERT_KEEP_DAYS="${NAVI_CONVERT_SCRATCH_KEEP_DAYS:-7}"
EXTRACT_KEEP_DAYS="${NAVI_EXTRACT_KEEP_DAYS:-21}"
STAGING_KEEP_DAYS="${NAVI_STAGING_KEEP_DAYS:-2}"

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
SCRUB_LOG="${NAVI_LOG_DIR}/scrub-${RUN_ID}.log"
mkdir -p "$NAVI_LOG_DIR"

if [[ "$REPORT_ONLY" -eq 0 ]]; then
  atomic_log_begin "$SCRUB_LOG"
fi

scrub_fail() {
  local rc=$?
  if [[ $rc -ne 0 ]]; then
    log_fail "scrub FAILED exit=${rc}"
    atomic_log_abort
  fi
}
trap scrub_fail EXIT

log_info "==== scrub start run_id=${RUN_ID} keep_gens=${KEEP} log_days=${LOG_KEEP_DAYS} convert_days=${CONVERT_KEEP_DAYS} extract_days=${EXTRACT_KEEP_DAYS} ===="

if [[ "$REPORT_ONLY" -eq 1 ]]; then
  "${SCRIPT_DIR}/check-disk-quota.sh" --report-only
  log_info "report-only; nothing pruned"
  trap - EXIT
  exit 0
fi

# Scrub runs even when near full (that is when pruning helps most).
# Weekly bake still uses the hard gate separately.
if [[ "$SKIP_QUOTA_GATE" -eq 0 ]]; then
  "${SCRIPT_DIR}/check-disk-quota.sh" --report-only || true
fi

# Resolve protected generation paths.
protect=()
for link in "$NAVI_LIVE_LINK" "$NAVI_PREVIOUS_LINK"; do
  if [[ -L "$link" || -d "$link" ]]; then
    protect+=("$(readlink -f "$link")")
  fi
done

mapfile -t gens < <(find "$NAVI_GENERATIONS_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null | sort -nr | awk '{print $2}')

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
  gen_name="$(basename "$g")"
  if [[ -d "${NAVI_PUBLISHED_DIR}/packs" ]]; then
    find "${NAVI_PUBLISHED_DIR}/packs" -mindepth 2 -maxdepth 2 -type d -name "$gen_name" -print | while read -r pd; do
      log_info "prune published ${pd}"
      rm -rf "$pd"
    done
  fi
done

# Staging leftovers from crashed runs.
if [[ -d "$NAVI_STAGING_DIR" ]]; then
  find "$NAVI_STAGING_DIR" -mindepth 1 -maxdepth 1 -type d -mtime "+${STAGING_KEEP_DAYS}" -print | while read -r d; do
    log_info "prune stale staging ${d}"
    rm -rf "$d"
  done
fi

if [[ -d "${NAVI_PUBLISHED_DIR}/.staging" ]]; then
  rm -rf "${NAVI_PUBLISHED_DIR}/.staging"
fi

# Convert scratch (tiled spills, incomplete regions).
if [[ -d "$NAVI_CONVERT_DIR" ]]; then
  log_info "pruning convert scratch older than ${CONVERT_KEEP_DAYS}d under ${NAVI_CONVERT_DIR}"
  find "$NAVI_CONVERT_DIR" -mindepth 1 -maxdepth 1 -mtime "+${CONVERT_KEEP_DAYS}" -print | while read -r d; do
    log_info "prune convert ${d}"
    rm -rf "$d"
  done
  # Orphan spill / lock crumbs inside kept dirs.
  find "$NAVI_CONVERT_DIR" -type f \( \
      -name 'navi-tiled-ways*.bin' \
      -o -name '*.partial' \
      -o -name '*.navi-region-lock.json' \
    \) -mtime "+${CONVERT_KEEP_DAYS}" -print -delete 2>/dev/null || true
fi

if [[ "$PRUNE_EXTRACTS" -eq 1 && -d "$NAVI_EXTRACTS_DIR" ]]; then
  log_info "pruning extracts older than ${EXTRACT_KEEP_DAYS}d under ${NAVI_EXTRACTS_DIR}"
  find "$NAVI_EXTRACTS_DIR" -type f \( -name '*.osm.pbf' -o -name '*.md5' -o -name '*.poly' -o -name '*.poly.partial' \) \
    -mtime "+${EXTRACT_KEEP_DAYS}" -print -delete
fi

# Bake / scrub logs (keep recent; drop stale partials always).
if [[ -d "$NAVI_LOG_DIR" ]]; then
  log_info "pruning logs older than ${LOG_KEEP_DAYS}d under ${NAVI_LOG_DIR}"
  find "$NAVI_LOG_DIR" -type f \( -name 'weekly-*.log' -o -name 'scrub-*.log' -o -name '*-concurrency-*.log' \) \
    -mtime "+${LOG_KEEP_DAYS}" -print -delete
  # Partial logs older than 1 day = abandoned run.
  find "$NAVI_LOG_DIR" -type f -name '*.partial' -mtime +1 -print -delete
fi

# State crumbs.
if [[ -d "$NAVI_STATE_DIR" ]]; then
  find "$NAVI_STATE_DIR" -type f -name '*.partial' -mtime +1 -print -delete 2>/dev/null || true
fi

log_info "disk usage:"
du -sh "$NAVI_PACK_ROOT"/* 2>/dev/null || true
"${SCRIPT_DIR}/check-disk-quota.sh" --report-only || true

log_info "scrub complete"
atomic_log_commit
trap - EXIT
exit 0
