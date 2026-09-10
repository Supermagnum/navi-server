#!/usr/bin/env bash
# Self-maintaining scrub: prune outdated generations, scratch, logs,
# locks, and partial files; then report disk space (filesystem or optional ZFS).
#
# Held .osm.pbf extracts are NOT age-deleted by default (needed for Geofabrik
# incremental). Opt in to age-delete with:
#   --prune-extracts
#   or NAVI_SCRUB_PRUNE_EXTRACTS=1 in config.env / the environment
#
# Independent of age-delete, cleanup always enforces the held-PBF disk policy
# (unless both knobs are 0 / unlimited):
#   NAVI_HELD_PBF_MAX_MIB   — drop any held *.osm.pbf larger than this (0=off)
#   NAVI_HELD_PBF_BUDGET_GIB — keep total held PBF bytes under this GiB by
#     preferring smaller files (drop largest first when over; 0=unlimited)
# Never deletes *.partial / *incremental.partial* (in-flight temps).
#
# Usage:
#   ./cleanup.sh                 # scrub gens/convert/logs; budget-cap extracts
#   ./cleanup.sh --keep 2
#   ./cleanup.sh --no-extracts   # skip age-delete (same as default); still budget
#   ./cleanup.sh --prune-extracts  # age-delete extracts (opt-in); then budget
#   ./cleanup.sh --report-only   # disk report only, no deletes
#
# Installed as a daily systemd timer by setup-server.sh --apply-service
# (navi-pack-scrub.timer; ExecStart passes --no-extracts). Also runs at the
# end of each weekly bake with --no-extracts.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

# Safe default: never age-delete held PBFs unless explicitly opted in.
# CLI flags below override NAVI_SCRUB_PRUNE_EXTRACTS.
PRUNE_EXTRACTS=0
case "${NAVI_SCRUB_PRUNE_EXTRACTS:-0}" in
  1|true|TRUE|yes|YES|on|ON) PRUNE_EXTRACTS=1 ;;
esac

KEEP="${NAVI_KEEP_GENERATIONS}"
REPORT_ONLY=0
SKIP_QUOTA_GATE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prune-extracts) PRUNE_EXTRACTS=1; shift ;;
    --no-extracts) PRUNE_EXTRACTS=0; shift ;;
    --keep) KEEP="$2"; shift 2 ;;
    --report-only) REPORT_ONLY=1; shift ;;
    --skip-quota-gate) SKIP_QUOTA_GATE=1; shift ;;
    -h|--help)
      sed -n '2,22p' "$0"
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
  log_info "pruning extracts older than ${EXTRACT_KEEP_DAYS}d under ${NAVI_EXTRACTS_DIR} (opt-in)"
  find "$NAVI_EXTRACTS_DIR" -type f \( -name '*.osm.pbf' -o -name '*.md5' -o -name '*.poly' -o -name '*.poly.partial' \) \
    -mtime "+${EXTRACT_KEEP_DAYS}" -print -delete
else
  log_info "extract age-prune skipped (default; pass --prune-extracts or NAVI_SCRUB_PRUNE_EXTRACTS=1 to age-delete held PBFs)"
fi

# --- Held-PBF retention policy (size cap + total budget) ---
# Regions without a held PBF fall back to full Geofabrik fetch automatically.
# Prefer keeping smaller extracts so more regions stay on the incremental path
# within the 512 GB VPS envelope.
HELD_BUDGET_GIB="${NAVI_HELD_PBF_BUDGET_GIB:-0}"
HELD_MAX_MIB="${NAVI_HELD_PBF_MAX_MIB:-0}"

remove_held_pbf_and_companions() {
  local pbf="$1"
  local reason="$2"
  local base stem poly
  [[ -f "$pbf" ]] || return 0
  # Never touch in-flight temps.
  case "$(basename "$pbf")" in
    *partial*) return 0 ;;
  esac
  log_info "held-pbf drop reason=${reason} path=${pbf} bytes=$(stat -c '%s' "$pbf" 2>/dev/null || echo '?')"
  rm -f "$pbf" "${pbf}.md5"
  base="$(basename "$pbf")"
  stem="${base%-latest.osm.pbf}"
  if [[ "$stem" != "$base" ]]; then
    poly="${NAVI_EXTRACTS_DIR}/${stem}.poly"
    rm -f "$poly"
  fi
  return 0
}

if [[ -d "$NAVI_EXTRACTS_DIR" ]]; then
  mapfile -t _held_pbfs < <(
    find "$NAVI_EXTRACTS_DIR" -maxdepth 1 -type f -name '*-latest.osm.pbf' ! -name '*partial*' -printf '%s %p\n' 2>/dev/null \
      | sort -nr
  )

  if [[ "${HELD_MAX_MIB}" =~ ^[0-9]+$ && "$HELD_MAX_MIB" -gt 0 ]]; then
    _max_bytes=$((HELD_MAX_MIB * 1024 * 1024))
    log_info "held-pbf max-size policy max_mib=${HELD_MAX_MIB}"
    for _entry in "${_held_pbfs[@]+"${_held_pbfs[@]}"}"; do
      [[ -n "${_entry:-}" ]] || continue
      _sz="${_entry%% *}"
      _path="${_entry#* }"
      if [[ "$_sz" -gt "$_max_bytes" ]]; then
        remove_held_pbf_and_companions "$_path" "over_max_mib=${HELD_MAX_MIB}"
      fi
    done
    # Refresh list after max-size drops.
    mapfile -t _held_pbfs < <(
      find "$NAVI_EXTRACTS_DIR" -maxdepth 1 -type f -name '*-latest.osm.pbf' ! -name '*partial*' -printf '%s %p\n' 2>/dev/null \
        | sort -nr
    )
  else
    log_info "held-pbf max-size policy disabled (NAVI_HELD_PBF_MAX_MIB=0)"
  fi

  if [[ "${HELD_BUDGET_GIB}" =~ ^[0-9]+$ && "$HELD_BUDGET_GIB" -gt 0 ]]; then
    _budget_bytes=$((HELD_BUDGET_GIB * 1024 * 1024 * 1024))
    _total=0
    for _entry in "${_held_pbfs[@]+"${_held_pbfs[@]}"}"; do
      [[ -n "${_entry:-}" ]] || continue
      _total=$((_total + ${_entry%% *}))
    done
    log_info "held-pbf budget policy budget_gib=${HELD_BUDGET_GIB} current_bytes=${_total} files=${#_held_pbfs[@]}"
    # Already sorted largest-first: drop from the top until under budget.
    for _entry in "${_held_pbfs[@]+"${_held_pbfs[@]}"}"; do
      [[ -n "${_entry:-}" ]] || continue
      if [[ "$_total" -le "$_budget_bytes" ]]; then
        break
      fi
      _sz="${_entry%% *}"
      _path="${_entry#* }"
      remove_held_pbf_and_companions "$_path" "over_budget_gib=${HELD_BUDGET_GIB}"
      _total=$((_total - _sz))
    done
    log_info "held-pbf budget after_bytes=${_total}"
  else
    log_info "held-pbf budget policy disabled (NAVI_HELD_PBF_BUDGET_GIB=0)"
  fi
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
