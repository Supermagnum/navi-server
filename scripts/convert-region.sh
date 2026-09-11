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
# Δh default comes from data/config.env (NAVI_BAKE_DELTA_H=1). When on,
# NAVI_ELEV_DIR (or --elev-dir) must point at a DEM tile directory.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

OUT_DIR=""
PROFILES="${NAVI_PROFILES}"
ELEV_DIR="${NAVI_ELEV_DIR}"
BAKE_DELTA_H="${NAVI_BAKE_DELTA_H}"
DO_ALL=0
FILTER_IDS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all) DO_ALL=1; shift ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --profiles) PROFILES="$2"; shift 2 ;;
    --elev-dir) ELEV_DIR="$2"; BAKE_DELTA_H=1; shift 2 ;;
    --delta-h)
      BAKE_DELTA_H=1
      if [[ -z "${ELEV_DIR}" && -z "${NAVI_ELEV_DIR}" ]]; then
        die "--delta-h requires NAVI_ELEV_DIR in config.env or --elev-dir"
      fi
      ELEV_DIR="${ELEV_DIR:-$NAVI_ELEV_DIR}"
      shift
      ;;
    --no-delta-h)
      BAKE_DELTA_H=0
      ELEV_DIR=""
      shift
      ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *) FILTER_IDS+=("$1"); shift ;;
  esac
done

if [[ "$DO_ALL" -eq 0 && ${#FILTER_IDS[@]} -eq 0 ]]; then
  die "pass a region id or --all"
fi

# Config default is Δh on — require a DEM dir unless explicitly disabled.
if [[ "$BAKE_DELTA_H" == "1" ]]; then
  ELEV_DIR="${ELEV_DIR:-$NAVI_ELEV_DIR}"
  if [[ -z "$ELEV_DIR" ]]; then
    die "NAVI_BAKE_DELTA_H=1 but NAVI_ELEV_DIR is empty — set DEM path in ${NAVI_PACK_ROOT}/config.env (or pass --elev-dir / --no-delta-h)"
  fi
  [[ -d "$ELEV_DIR" ]] || die "elev dir missing: $ELEV_DIR (set NAVI_ELEV_DIR in config.env)"
else
  ELEV_DIR=""
fi

CONVERT_BIN="$(resolve_convert_bin)"
log_info "using convert binary: ${CONVERT_BIN}"

convert_one() {
  local region_id="$1"
  local pbf out elev_args
  pbf="$(region_pbf_path "$region_id")"
  [[ -f "$pbf" ]] || die "missing extract for ${region_id}: ${pbf} (run fetch-extracts.sh first)"

  if [[ -n "$OUT_DIR" ]]; then
    out="${OUT_DIR}/${region_id}"
  else
    out="${NAVI_CONVERT_DIR}/${region_id}"
  fi
  mkdir -p "$out"

  elev_args=()
  if [[ -n "$ELEV_DIR" ]]; then
    elev_args=(--elev-dir "$ELEV_DIR")
    log_info "convert region=${region_id} delta_h=yes elev_dir=${ELEV_DIR}"
  else
    log_info "convert region=${region_id} delta_h=no profiles=${PROFILES}"
  fi

  # Existing CLI contract — do not invent new flags on the binary.
  "$CONVERT_BIN" \
    --data-dir "$out" \
    --pbf "$pbf" \
    --profiles "$PROFILES" \
    "${elev_args[@]+"${elev_args[@]}"}"

  # Record convert metadata for publish/validate.
  local meta="${out}/.convert-meta.json"
  python3 - "$region_id" "$pbf" "$out" "$PROFILES" "${ELEV_DIR:-}" "$meta" <<'PY'
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
  log_info "convert OK region=${region_id} out=${out}"
}

matched=0
skipped=0
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
  convert_one "$region_id"
done

if [[ "$matched" -eq 0 && "$skipped" -eq 0 ]]; then
  die "no matching regions"
fi
if [[ "$matched" -eq 0 && "$skipped" -gt 0 ]]; then
  log_info "convert step complete (all matching regions skipped skip_reason; count=${skipped})"
  exit 0
fi

log_info "convert step complete"
