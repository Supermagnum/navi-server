#!/usr/bin/env bash
# Optional town-to-town OD route cache bake.
# No OD builder exists in the Navi tree yet (see docs/precomputed-index-and-route-cache.md).
# This step is a versioned hook: when NAVI_TOWN_ROUTE_BIN is set, it runs per region
# against the same pack generation directory so stale routes drop with stale packs.
#
# Usage:
#   ./bake-town-routes.sh --generation-dir /path/to/gen hedmark
#   ./bake-town-routes.sh --generation-dir /path/to/gen --all

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

GEN_DIR=""
DO_ALL=0
FILTER_IDS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --generation-dir) GEN_DIR="$2"; shift 2 ;;
    --all) DO_ALL=1; shift ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
    *) FILTER_IDS+=("$1"); shift ;;
  esac
done

[[ -n "$GEN_DIR" ]] || die "--generation-dir is required"
[[ -d "$GEN_DIR" ]] || die "generation dir missing: $GEN_DIR"

if [[ "${NAVI_BAKE_TOWN_ROUTES}" != "1" ]]; then
  log_info "town-route bake disabled (NAVI_BAKE_TOWN_ROUTES!=1); skipping"
  exit 0
fi

if [[ -z "${NAVI_TOWN_ROUTE_BIN}" ]]; then
  log_warn "NAVI_BAKE_TOWN_ROUTES=1 but NAVI_TOWN_ROUTE_BIN unset — no OD builder in-tree; skipping"
  exit 0
fi

[[ -x "${NAVI_TOWN_ROUTE_BIN}" ]] || die "town route binary not executable: ${NAVI_TOWN_ROUTE_BIN}"

if [[ "$DO_ALL" -eq 0 && ${#FILTER_IDS[@]} -eq 0 ]]; then
  die "pass a region id or --all"
fi

bake_one() {
  local region_id="$1"
  local region_dir="${GEN_DIR}/regions/${region_id}"
  local route_dir="${GEN_DIR}/town-routes/${region_id}"
  mkdir -p "$route_dir"
  [[ -d "$region_dir" ]] || die "missing region packs for ${region_id}: ${region_dir}"
  log_info "town-route bake region=${region_id}"
  # Contract for a future baker: packs in, routes out, same generation id.
  "$NAVI_TOWN_ROUTE_BIN" \
    --packs-dir "$region_dir" \
    --out-dir "$route_dir" \
    --region-id "$region_id" \
    --generation "$(basename "$GEN_DIR")"
  log_info "town-route bake OK region=${region_id}"
}

matched=0
while IFS=$'\t' read -r region_id src; do
  if [[ "$DO_ALL" -eq 0 ]]; then
    keep=0
    for f in "${FILTER_IDS[@]}"; do
      [[ "$f" == "$region_id" ]] && keep=1
    done
    [[ "$keep" -eq 1 ]] || continue
  fi
  matched=1
  bake_one "$region_id"
done < <(list_regions)

[[ "$matched" -eq 1 ]] || die "no matching regions"
log_info "town-route step complete"
