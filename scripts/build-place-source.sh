#!/usr/bin/env bash
# Build {stem}.navi-place-source.osm.pbf (points variant) into convert output.
#
# Default OFF: NAVI_BAKE_PLACE_SOURCE=0 — this script exits 0 without writing.
# Soft-fail: any tool/error for a region is logged and that region is skipped;
# exit 0 so pack bake is never failed by place-source.
#
# Parallelism: NAVI_PLACE_SOURCE_PARALLEL (default 2). Trial B peaked ~5.8 GiB
# per region; 2 × 5.8 < 16 GiB host budget with headroom for the OS.
#
# Usage:
#   ./build-place-source.sh hedmark
#   ./build-place-source.sh --all
#   ./build-place-source.sh --region europe_norway_ostlandet

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

DO_ALL=0
FILTER_IDS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all) DO_ALL=1; shift ;;
    --region) FILTER_IDS+=("$2"); shift 2 ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    *) FILTER_IDS+=("$1"); shift ;;
  esac
done

if [[ "${NAVI_BAKE_PLACE_SOURCE:-0}" != "1" ]]; then
  log_info "place-source skipped (NAVI_BAKE_PLACE_SOURCE=${NAVI_BAKE_PLACE_SOURCE:-0})"
  exit 0
fi

if [[ "$DO_ALL" -eq 0 && ${#FILTER_IDS[@]} -eq 0 ]]; then
  die "pass a region id or --all"
fi

resolve_place_source_bin() {
  if [[ -n "${NAVI_PLACE_SOURCE_BIN:-}" && -x "${NAVI_PLACE_SOURCE_BIN}" ]]; then
    printf '%s\n' "${NAVI_PLACE_SOURCE_BIN}"
    return 0
  fi
  local cand
  for cand in \
    "${SCRIPT_DIR}/../target/release/place-source-points" \
    "${SCRIPT_DIR}/../target/debug/place-source-points"
  do
    if [[ -x "$cand" ]]; then
      printf '%s\n' "$cand"
      return 0
    fi
  done
  return 1
}

place_source_enabled_for_region() {
  local rid="$1"
  local list="${NAVI_PLACE_SOURCE_REGIONS:-}"
  [[ -z "$list" ]] && return 0
  local IFS=','
  local id
  for id in $list; do
    id="$(echo "$id" | tr -d '[:space:]')"
    [[ "$id" == "$rid" ]] && return 0
  done
  return 1
}

BIN="$(resolve_place_source_bin || true)"
if [[ -z "${BIN}" ]]; then
  log_warn "place-source binary missing — packs continue without place-source (build with: cargo build --release -p place-source-points)"
  exit 0
fi
if ! command -v osmium >/dev/null 2>&1; then
  log_warn "place-source requires osmium on PATH — packs continue without place-source"
  exit 0
fi

PARALLEL="${NAVI_PLACE_SOURCE_PARALLEL:-2}"
if ! [[ "$PARALLEL" =~ ^[0-9]+$ ]] || [[ "$PARALLEL" -lt 1 ]]; then
  PARALLEL=1
fi
# Hard cap: keep total peak under ~16 GiB (trial B ~5.8 GiB/region).
if [[ "$PARALLEL" -gt 2 ]]; then
  log_warn "place-source parallel=${PARALLEL} capped to 2 (16 GiB RAM budget)"
  PARALLEL=2
fi

log_info "place-source bin=${BIN} parallel=${PARALLEL}"

build_one() {
  local region_id="$1"
  local pbf out stem dest
  if ! place_source_enabled_for_region "$region_id"; then
    log_info "place-source skip region=${region_id} (not in NAVI_PLACE_SOURCE_REGIONS)"
    return 0
  fi
  pbf="$(region_pbf_path "$region_id")"
  if [[ ! -f "$pbf" ]]; then
    log_warn "place-source skip region=${region_id} reason=missing_extract path=${pbf}"
    return 0
  fi
  out="${NAVI_CONVERT_DIR}/${region_id}"
  if [[ ! -d "$out" ]]; then
    # Allow building into convert dir even before convert (step-2 style);
    # create the directory so the file can be staged later with packs.
    mkdir -p "$out"
  fi
  stem="$(basename "$pbf" .osm.pbf)"
  dest="${out}/${stem}.navi-place-source.osm.pbf"
  set +e
  "$BIN" "$pbf" "$dest"
  local rc=$?
  set -e
  if [[ "$rc" -ne 0 ]]; then
    log_warn "place-source FAIL region=${region_id} rc=${rc} — packs continue without place-source"
    rm -f "$dest"
    return 0
  fi
  if [[ ! -s "$dest" ]]; then
    log_warn "place-source empty region=${region_id} — removing"
    rm -f "$dest"
    return 0
  fi
  log_info "place-source OK region=${region_id} out=${dest} bytes=$(stat -c %s "$dest" 2>/dev/null || echo '?')"
  return 0
}

WORK=()
while IFS=$'\t' read -r region_id _src; do
  if [[ "$DO_ALL" -eq 0 ]]; then
    keep=0
    for f in "${FILTER_IDS[@]}"; do
      [[ "$f" == "$region_id" ]] && keep=1
    done
    [[ "$keep" -eq 1 ]] || continue
  fi
  if region_skip_if_tagged "$region_id"; then
    continue
  fi
  WORK+=("$region_id")
done < <(list_regions)

if [[ ${#WORK[@]} -eq 0 ]]; then
  log_info "place-source: no matching regions"
  exit 0
fi

# Bounded job pool (default parallel=2). Each job soft-fails inside build_one.
running=0
for region_id in "${WORK[@]}"; do
  build_one "$region_id" &
  running=$((running + 1))
  if [[ "$running" -ge "$PARALLEL" ]]; then
    wait -n 2>/dev/null || wait
    running=$((running - 1))
  fi
done
wait

exit 0
