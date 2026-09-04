#!/usr/bin/env bash
# Shared helpers for the Navi server pack bake pipeline.
# Source from sibling scripts:  source "$(dirname "$0")/lib/common.sh"

set -euo pipefail

# common.sh lives at navi-server/scripts/lib/common.sh
NAVI_SERVER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Convert sources stay in the Navi app repo (sibling of navi-server on this box).
: "${NAVI_ROOT:=/media/navi/Navi}"

# Default data root inside this self-contained tree.
: "${NAVI_PACK_ROOT:=${NAVI_SERVER_ROOT}/data}"

load_config() {
  local cfg="${NAVI_PACK_CONFIG:-${NAVI_PACK_ROOT}/config.env}"
  if [[ -f "$cfg" ]]; then
    # shellcheck disable=SC1090
    set -a
    source "$cfg"
    set +a
  fi
  : "${NAVI_PACK_ROOT:=${NAVI_SERVER_ROOT}/data}"
  : "${NAVI_REGIONS_CONF:=${NAVI_PACK_ROOT}/regions.conf}"
  : "${NAVI_SCRATCH_DIR:=${NAVI_PACK_ROOT}/scratch}"
  : "${NAVI_EXTRACTS_DIR:=${NAVI_SCRATCH_DIR}/extracts}"
  : "${NAVI_CONVERT_DIR:=${NAVI_SCRATCH_DIR}/convert}"
  : "${NAVI_STATE_DIR:=${NAVI_PACK_ROOT}/state}"
  : "${NAVI_STAGING_DIR:=${NAVI_PACK_ROOT}/staging}"
  : "${NAVI_GENERATIONS_DIR:=${NAVI_PACK_ROOT}/generations}"
  : "${NAVI_LOG_DIR:=${NAVI_PACK_ROOT}/logs}"
  : "${NAVI_LIVE_LINK:=${NAVI_PACK_ROOT}/live}"
  : "${NAVI_PREVIOUS_LINK:=${NAVI_PACK_ROOT}/previous}"
  # Only this tree is safe to expose over HTTP (static GET/HEAD). Never put
  # scripts/, scratch/, staging/, state/, or systemd/ under it.
  : "${NAVI_PUBLISHED_DIR:=${NAVI_PACK_ROOT}/published}"
  : "${NAVI_PROFILES:=car,foot}"
  : "${NAVI_ELEV_DIR:=${NAVI_PACK_ROOT}/elevation}"
  # Bake edge_delta_h_m into graph packs (default on). Set to 0 to disable.
  # When on, NAVI_ELEV_DIR must point at a DEM tile directory.
  : "${NAVI_BAKE_DELTA_H:=1}"
  : "${NAVI_BAKE_TOWN_ROUTES:=0}"
  : "${NAVI_TOWN_ROUTE_BIN:=}"
  : "${NAVI_CONVERT_BIN:=}"
  : "${NAVI_KEEP_GENERATIONS:=2}"
  : "${NAVI_QUOTA_WARN_PCT:=85}"
  : "${NAVI_QUOTA_FAIL_PCT:=95}"
  : "${NAVI_ZFS_DATASET:=Mypool/navi}"
  : "${NAVI_GEOFABRIK_BASE:=https://download.geofabrik.de}"
  # Size sanity bands vs source PBF (MiB/MiB). Wide on purpose — Hedmark ratios
  # are sizing targets, not hard requirements for every region.
  : "${NAVI_SIZE_GRAPH_MIN_RATIO:=0.10}"
  : "${NAVI_SIZE_GRAPH_MAX_RATIO:=20.0}"
  : "${NAVI_SIZE_POI_MIN_RATIO:=0.01}"
  : "${NAVI_SIZE_POI_MAX_RATIO:=0.50}"
  : "${NAVI_SIZE_WETLAND_MIN_RATIO:=0.001}"
  : "${NAVI_SIZE_WETLAND_MAX_RATIO:=0.50}"
  : "${NAVI_SIZE_TOTAL_MIN_RATIO:=0.05}"
  : "${NAVI_SIZE_TOTAL_MAX_RATIO:=25.0}"
  : "${NAVI_SIZE_VS_PREV_MAX_FACTOR:=3.0}"
  : "${NAVI_HTTP_TIMEOUT_SECS:=120}"
  mkdir -p \
    "$NAVI_EXTRACTS_DIR" \
    "$NAVI_CONVERT_DIR" \
    "$NAVI_STATE_DIR" \
    "$NAVI_STAGING_DIR" \
    "$NAVI_GENERATIONS_DIR" \
    "$NAVI_LOG_DIR" \
    "${NAVI_PUBLISHED_DIR}/packs"
}

log() {
  local level="$1"
  shift
  printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" "$*"
}

log_info() { log INFO "$@"; }
log_warn() { log WARN "$@"; }
log_error() { log ERROR "$@"; }
log_fail() { log FAILED "$@"; }

die() {
  log_fail "$@"
  exit 1
}

require_cmd() {
  local c
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 || die "required command not found: $c"
  done
}

# Resolve navi-indexed-convert without modifying the existing convert tooling.
resolve_convert_bin() {
  if [[ -n "${NAVI_CONVERT_BIN}" && -x "${NAVI_CONVERT_BIN}" ]]; then
    printf '%s\n' "$NAVI_CONVERT_BIN"
    return 0
  fi
  local candidates=(
    "${NAVI_ROOT}/target/release/navi-indexed-convert"
    "${NAVI_ROOT}/target/debug/navi-indexed-convert"
  )
  local c
  for c in "${candidates[@]}"; do
    if [[ -x "$c" ]]; then
      printf '%s\n' "$c"
      return 0
    fi
  done
  if command -v cargo >/dev/null 2>&1; then
    log_info "building navi-indexed-convert (release) via cargo"
    (cd "$NAVI_ROOT" && cargo build -p navi-ffi --release --bin navi-indexed-convert) >&2
    if [[ -x "${NAVI_ROOT}/target/release/navi-indexed-convert" ]]; then
      printf '%s\n' "${NAVI_ROOT}/target/release/navi-indexed-convert"
      return 0
    fi
  elif [[ -x "${HOME}/.cargo/bin/cargo" ]]; then
    log_info "building navi-indexed-convert (release) via ~/.cargo/bin/cargo"
    (cd "$NAVI_ROOT" && "${HOME}/.cargo/bin/cargo" build -p navi-ffi --release --bin navi-indexed-convert) >&2
    if [[ -x "${NAVI_ROOT}/target/release/navi-indexed-convert" ]]; then
      printf '%s\n' "${NAVI_ROOT}/target/release/navi-indexed-convert"
      return 0
    fi
  fi
  die "navi-indexed-convert not found; set NAVI_CONVERT_BIN or build with: cargo build -p navi-ffi --release --bin navi-indexed-convert"
}

# regions.conf lines:
#   region_id<TAB>geofabrik:<path>
#   region_id<TAB>url:<https://...>
#   region_id<TAB>planet   (planet-latest from planet.openstreetmap.org)
# Blank lines and # comments ignored.
list_regions() {
  local conf="${1:-$NAVI_REGIONS_CONF}"
  [[ -f "$conf" ]] || die "regions config missing: $conf"
  awk '
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    {
      id=$1
      $1=""
      sub(/^[[:space:]]+/, "", $0)
      if (id == "" || $0 == "") next
      print id "\t" $0
    }
  ' "$conf"
}

region_source_kind() {
  local src="$1"
  case "$src" in
    geofabrik:*) echo geofabrik ;;
    url:*) echo url ;;
    planet) echo planet ;;
    *) echo unknown ;;
  esac
}

region_pbf_url() {
  local src="$1"
  case "$src" in
    geofabrik:*)
      local path="${src#geofabrik:}"
      path="${path#/}"
      printf '%s/%s-latest.osm.pbf\n' "$NAVI_GEOFABRIK_BASE" "$path"
      ;;
    url:*)
      printf '%s\n' "${src#url:}"
      ;;
    planet)
      printf 'https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf\n'
      ;;
    *)
      die "unsupported region source: $src"
      ;;
  esac
}

region_md5_url() {
  local src="$1"
  case "$src" in
    geofabrik:*)
      printf '%s.md5\n' "$(region_pbf_url "$src")"
      ;;
    planet)
      printf 'https://planet.openstreetmap.org/pbf/planet-latest.osm.pbf.md5\n'
      ;;
    *)
      # OSM.fr and custom URLs often omit published checksums.
      return 1
      ;;
  esac
}

region_pbf_path() {
  local region_id="$1"
  printf '%s/%s-latest.osm.pbf\n' "$NAVI_EXTRACTS_DIR" "$region_id"
}

region_state_dir() {
  local region_id="$1"
  printf '%s/regions/%s\n' "$NAVI_STATE_DIR" "$region_id"
}

generation_id() {
  date -u +%Y%m%dT%H%M%SZ
}

bytes_to_mib() {
  local b="$1"
  awk -v b="$b" 'BEGIN { printf "%.3f", b / (1024*1024) }'
}

file_size_bytes() {
  local f="$1"
  if [[ -f "$f" ]]; then
    stat -c '%s' "$f"
  else
    echo 0
  fi
}

dir_size_bytes() {
  local d="$1"
  if [[ -d "$d" ]]; then
    du -sb "$d" 2>/dev/null | awk '{print $1}'
  else
    echo 0
  fi
}
