#!/usr/bin/env bash
# Convert step: invoke existing navi-indexed-convert per region.
# Does NOT modify convert sources — only calls the binary.
#
# Usage:
#   ./convert-region.sh hedmark
#   ./convert-region.sh --all
#   ./convert-region.sh --elev-dir /path/to/dem hedmark   # bake edge_delta_h_m
#   ./convert-region.sh --no-delta-h hedmark              # override config (Δh off)
#   ./convert-region.sh --profiles car,truck,foot,bicycle hedmark
#   ./convert-region.sh --out-dir /path/to/staging/gen/regions hedmark
#
# Δh default comes from data/config.env (NAVI_BAKE_DELTA_H=1). Per-region
# trailing delta_h=0 on regions.conf disables Δh for that region only.
# When Δh is on: build road-cell list from the PBF, prefetch Copernicus tiles
# (no --evict), then require every cell present or known-404 before convert.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

OUT_DIR=""
PROFILES="${NAVI_PROFILES}"
ELEV_DIR="${NAVI_ELEV_DIR}"
# Global default; per-region override applied inside convert_one.
BAKE_DELTA_H_GLOBAL="${NAVI_BAKE_DELTA_H}"
DO_ALL=0
FILTER_IDS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all) DO_ALL=1; shift ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --profiles) PROFILES="$2"; shift 2 ;;
    --elev-dir) ELEV_DIR="$2"; BAKE_DELTA_H_GLOBAL=1; shift 2 ;;
    --delta-h)
      BAKE_DELTA_H_GLOBAL=1
      if [[ -z "${ELEV_DIR}" && -z "${NAVI_ELEV_DIR}" ]]; then
        die "--delta-h requires NAVI_ELEV_DIR in config.env or --elev-dir"
      fi
      ELEV_DIR="${ELEV_DIR:-$NAVI_ELEV_DIR}"
      shift
      ;;
    --no-delta-h)
      BAKE_DELTA_H_GLOBAL=0
      ELEV_DIR=""
      shift
      ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *) FILTER_IDS+=("$1"); shift ;;
  esac
done

if [[ "$DO_ALL" -eq 0 && ${#FILTER_IDS[@]} -eq 0 ]]; then
  die "pass a region id or --all"
fi

if [[ "$BAKE_DELTA_H_GLOBAL" == "1" ]]; then
  ELEV_DIR="${ELEV_DIR:-$NAVI_ELEV_DIR}"
  if [[ -z "$ELEV_DIR" ]]; then
    die "NAVI_BAKE_DELTA_H=1 but NAVI_ELEV_DIR is empty — set DEM path in ${NAVI_PACK_ROOT}/config.env (or pass --elev-dir / --no-delta-h)"
  fi
  mkdir -p "$ELEV_DIR"
else
  ELEV_DIR=""
fi

CONVERT_BIN="$(resolve_convert_bin)"
log_info "using convert binary: ${CONVERT_BIN}"

# Default off. NAVI_BAKE_FERRY_LINKS=1 enables for every region in this convert.
# Otherwise NAVI_FERRY_LINKS_REGIONS is a comma-separated bake_id allowlist.
ferry_links_enabled_for_region() {
  local rid="$1"
  local all="${NAVI_BAKE_FERRY_LINKS:-0}"
  case "$all" in
    1|true|TRUE|yes|YES|on|ON) return 0 ;;
  esac
  local list="${NAVI_FERRY_LINKS_REGIONS:-}"
  [[ -n "$list" ]] || return 1
  local IFS=','
  local id
  for id in $list; do
    id="$(echo "$id" | tr -d '[:space:]')"
    [[ "$id" == "$rid" ]] && return 0
  done
  return 1
}

# Returns 0 = converted, 2 = skipped (DEM / missing PBF soft), 1 = hard failure.
convert_one() {
  set +e
  local region_id="$1"
  local pbf out elev_args ferry_args cover_msg rc cells_path want_delta=0
  pbf="$(region_pbf_path "$region_id")"
  if [[ ! -f "$pbf" ]]; then
    log_warn "convert skip region=${region_id} reason=missing_extract path=${pbf}"
    return 2
  fi

  if [[ -n "$OUT_DIR" ]]; then
    out="${OUT_DIR}/${region_id}"
  else
    out="${NAVI_CONVERT_DIR}/${region_id}"
  fi
  mkdir -p "$out"

  elev_args=()
  NAVI_BAKE_DELTA_H="$BAKE_DELTA_H_GLOBAL"
  if region_delta_h_enabled "$region_id"; then
    want_delta=1
  fi

  if [[ "$want_delta" -eq 1 && -n "$ELEV_DIR" ]]; then
    prefetch_region_dem_cells "$region_id" "$ELEV_DIR"
    cells_path="$(region_dem_cells_path "$region_id")"
    cover_msg="$(
      PYTHONPATH="${SCRIPT_DIR}/lib${PYTHONPATH:+:$PYTHONPATH}" \
        python3 - "$ELEV_DIR" "$region_id" "$cells_path" "${NAVI_REGIONS_CONF}.bboxes.json" <<'PY'
import sys
from pathlib import Path
from dem_coverage import region_dem_ok
elev, rid, cells, bbox = sys.argv[1], sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
ok, msg = region_dem_ok(
    Path(elev),
    rid,
    cells_path=cells if cells.is_file() else None,
    bboxes_path=bbox if bbox.is_file() else None,
)
print(msg)
sys.exit(0 if ok else 2)
PY
    )"
    rc=$?
    if [[ "$rc" -ne 0 ]]; then
      log_warn "convert skip region=${region_id} reason=dem_coverage ${cover_msg}"
      rm -rf "$out"
      return 2
    fi
    log_info "convert region=${region_id} delta_h=yes elev_dir=${ELEV_DIR} ${cover_msg}"
    elev_args=(--elev-dir "$ELEV_DIR")
  else
    log_info "convert region=${region_id} delta_h=no profiles=${PROFILES}"
  fi

  # Opt-in ferry terminal boarding links (default off — Monday weekly must not
  # silently change car-graph topology for every region). Enable with
  # NAVI_BAKE_FERRY_LINKS=1 (all regions this convert) or
  # NAVI_FERRY_LINKS_REGIONS=id1,id2,... (comma list).
  ferry_args=()
  if ferry_links_enabled_for_region "$region_id"; then
    ferry_args=(--ferry-links)
    log_info "convert region=${region_id} ferry_links=yes"
  fi

  "$CONVERT_BIN" \
    --data-dir "$out" \
    --pbf "$pbf" \
    --profiles "$PROFILES" \
    "${elev_args[@]+"${elev_args[@]}"}" \
    "${ferry_args[@]+"${ferry_args[@]}"}"
  rc=$?
  if [[ "$rc" -ne 0 ]]; then
    log_warn "convert fail region=${region_id} rc=${rc} — continuing with other regions"
    rm -rf "$out"
    return 1
  fi

  local meta="${out}/.convert-meta.json"
  local elev_meta=""
  [[ "$want_delta" -eq 1 ]] && elev_meta="$ELEV_DIR"
  python3 - "$region_id" "$pbf" "$out" "$PROFILES" "${elev_meta}" "$meta" <<'PY'
import json, os, sys, time
region_id, pbf, out, profiles, elev, meta = sys.argv[1:7]
files = sorted(f for f in os.listdir(out) if not f.startswith("."))
payload = {
    "region_id": region_id,
    "pbf": pbf,
    "pbf_bytes": os.path.getsize(pbf),
    "out_dir": out,
    "profiles": profiles,
    "has_delta_h": bool(elev),
    "elev_dir": elev or None,
    "files": files,
    "converted_unix": int(time.time()),
}
with open(meta, "w", encoding="utf-8") as f:
    json.dump(payload, f, indent=2)
    f.write("\n")
PY
  rc=$?
  if [[ "$rc" -ne 0 ]]; then
    log_warn "convert meta fail region=${region_id} rc=${rc}"
    rm -rf "$out"
    return 1
  fi
  log_info "convert OK region=${region_id} out=${out}"
  return 0
}

matched=0
skipped=0
ok_n=0
dem_skip_n=0
fail_n=0
WORK=()
while IFS=$'\t' read -r region_id src; do
  if [[ "$DO_ALL" -eq 0 ]]; then
    keep=0
    for f in "${FILTER_IDS[@]}"; do
      [[ "$f" == "$region_id" ]] && keep=1
    done
    [[ "$keep" -eq 1 ]] || continue
  fi
  if region_skip_if_tagged "$region_id"; then
    skipped=$((skipped + 1))
    continue
  fi
  WORK+=("$region_id")
done < <(list_regions)

if [[ "$DO_ALL" -eq 1 ]]; then
  order_regions_array_follow_sun WORK
  log_info "convert sun_order=${NAVI_BAKE_SUN_ORDER:-1} regions=${#WORK[@]} skipped=${skipped}"
fi

for region_id in "${WORK[@]+"${WORK[@]}"}"; do
  matched=1
  set +e
  convert_one "$region_id"
  rc=$?
  set -e
  case "$rc" in
    0) ok_n=$((ok_n + 1)) ;;
    2) dem_skip_n=$((dem_skip_n + 1)) ;;
    *) fail_n=$((fail_n + 1)) ;;
  esac
done

if [[ "$matched" -eq 0 && "$skipped" -eq 0 ]]; then
  die "no matching regions"
fi
if [[ "$matched" -eq 0 && "$skipped" -gt 0 ]]; then
  log_info "convert step complete (all matching regions skipped skip_reason; count=${skipped})"
  exit 0
fi

log_info "convert step complete ok=${ok_n} dem_or_soft_skip=${dem_skip_n} fail=${fail_n} skip_reason=${skipped}"
exit 0
