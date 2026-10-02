#!/usr/bin/env bash
# Prefetch Copernicus DEM road-cells for weekly regions into NAVI_ELEV_DIR
# (standing cache; never --evict). Intended for tmux as navit-server.
#
# Usage:
#   ./fill-weekly-dem.sh                  # all non-skipped weekly regions
#   ./fill-weekly-dem.sh --region mexico  # one bake id
#   ./fill-weekly-dem.sh --force-cells    # rebuild cell lists even if cache hit
#
# Requires held PBFs under NAVI_EXTRACTS_DIR (run fetch-extracts.sh first).
# Existing DEM tiles are kept (prefetch skips non-empty .tif).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

FORCE_CELLS=0
FILTER_IDS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --region) FILTER_IDS+=("$2"); shift 2 ;;
    --force-cells) FORCE_CELLS=1; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
done

mkdir -p "$NAVI_ELEV_DIR" "$NAVI_STATE_DIR/dem_cells"
log_info "fill-weekly-dem elev=${NAVI_ELEV_DIR} state=${NAVI_STATE_DIR}/dem_cells"

ok_n=0
skip_n=0
fail_n=0
total_cells=0

while IFS=$'\t' read -r region_id src; do
  if [[ ${#FILTER_IDS[@]} -gt 0 ]]; then
    keep=0
    for f in "${FILTER_IDS[@]}"; do
      [[ "$f" == "$region_id" ]] && keep=1
    done
    [[ "$keep" -eq 1 ]] || continue
  fi
  if region_skip_if_tagged "$region_id"; then
    skip_n=$((skip_n + 1))
    continue
  fi
  if ! region_delta_h_enabled "$region_id"; then
    log_info "skip dem fill region=${region_id} reason=delta_h=0"
    skip_n=$((skip_n + 1))
    continue
  fi
  pbf="$(region_pbf_path "$region_id")"
  if [[ ! -f "$pbf" ]]; then
    log_warn "skip dem fill region=${region_id} reason=missing_pbf path=${pbf}"
    fail_n=$((fail_n + 1))
    continue
  fi
  cell_args=(--region "$region_id" --pbf "$pbf" --state-dir "$NAVI_STATE_DIR")
  [[ "$FORCE_CELLS" -eq 1 ]] && cell_args+=(--force)
  set +e
  python3 "${SCRIPT_DIR}/build-dem-cell-list.py" "${cell_args[@]}"
  rc=$?
  set -e
  if [[ "$rc" -ne 0 ]]; then
    log_warn "cell-list fail region=${region_id} rc=${rc}"
    fail_n=$((fail_n + 1))
    continue
  fi
  cells_path="$(region_dem_cells_path "$region_id")"
  ncells="$(python3 -c "import json;print(json.load(open('${cells_path}'))['cell_count'])")"
  total_cells=$((total_cells + ncells))
  log_info "prefetch region=${region_id} cells=${ncells}"
  set +e
  python3 "${SCRIPT_DIR}/prefetch-dem-bbox.py" \
    --elev-dir "$NAVI_ELEV_DIR" --cells-file "$cells_path"
  rc=$?
  set -e
  if [[ "$rc" -ne 0 ]]; then
    log_warn "prefetch fail region=${region_id} rc=${rc}"
    fail_n=$((fail_n + 1))
    continue
  fi
  ok_n=$((ok_n + 1))
done < <(list_regions)

log_info "fill-weekly-dem done ok=${ok_n} skip=${skip_n} fail=${fail_n} cells_sum=${total_cells}"
[[ "$fail_n" -eq 0 ]]
