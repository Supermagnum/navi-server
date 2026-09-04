#!/usr/bin/env bash
# Shared helpers for the Navi server pack bake pipeline.
# Source from sibling scripts:  source "$(dirname "$0")/lib/common.sh"

set -euo pipefail

# common.sh lives at navi-server/scripts/lib/common.sh
NAVI_SERVER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Default data root inside this self-contained tree.
: "${NAVI_PACK_ROOT:=${NAVI_SERVER_ROOT}/data}"

load_config() {
  local cfg="${NAVI_PACK_CONFIG:-${NAVI_PACK_ROOT}/config.env}"
  # Preserve explicit overrides set by the caller (e.g. planet smoke).
  local _ovr_regions="${NAVI_REGIONS_CONF-}"
  local _ovr_elev="${NAVI_ELEV_DIR-}"
  local _ovr_delta="${NAVI_BAKE_DELTA_H-}"
  local _ovr_pack_root="${NAVI_PACK_ROOT-}"
  if [[ -f "$cfg" ]]; then
    # shellcheck disable=SC1090
    set -a
    source "$cfg"
    set +a
  fi
  [[ -n "${_ovr_pack_root}" ]] && NAVI_PACK_ROOT="${_ovr_pack_root}"
  [[ -n "${_ovr_regions}" ]] && NAVI_REGIONS_CONF="${_ovr_regions}"
  [[ -n "${_ovr_elev}" ]] && NAVI_ELEV_DIR="${_ovr_elev}"
  [[ -n "${_ovr_delta}" ]] && NAVI_BAKE_DELTA_H="${_ovr_delta}"
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
  : "${NAVI_SIZE_POI_MAX_RATIO:=1.50}"
  : "${NAVI_SIZE_WETLAND_MIN_RATIO:=0.0}"
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

# Resolve in-repo navi-indexed-convert (pack-convert-core / navi-indexed-convert).
# Builds from this tree only — no Navi / navi-ffi / NAVI_ROOT / NAVI_CONVERT_BIN.
resolve_convert_bin() {
  local release_bin="${NAVI_SERVER_ROOT}/target/release/navi-indexed-convert"
  local debug_bin="${NAVI_SERVER_ROOT}/target/debug/navi-indexed-convert"
  if [[ -x "$release_bin" ]]; then
    printf '%s\n' "$release_bin"
    return 0
  fi
  if [[ -x "$debug_bin" ]]; then
    printf '%s\n' "$debug_bin"
    return 0
  fi
  local cargo_bin=""
  if command -v cargo >/dev/null 2>&1; then
    cargo_bin="$(command -v cargo)"
  elif [[ -x "${HOME}/.cargo/bin/cargo" ]]; then
    cargo_bin="${HOME}/.cargo/bin/cargo"
  fi
  if [[ -n "$cargo_bin" ]]; then
    log_info "building navi-indexed-convert (release) in ${NAVI_SERVER_ROOT}"
    # Force in-repo target dir so a host CARGO_TARGET_DIR pointing at Navi cannot leak in.
    (cd "$NAVI_SERVER_ROOT" && CARGO_TARGET_DIR="${NAVI_SERVER_ROOT}/target" \
      "$cargo_bin" build --release -p navi-indexed-convert) >&2
    if [[ -x "$release_bin" ]]; then
      printf '%s\n' "$release_bin"
      return 0
    fi
  fi
  die "navi-indexed-convert not found; build with: cd ${NAVI_SERVER_ROOT} && cargo build --release -p navi-indexed-convert"
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

# Collision-proof under concurrent callers: UTC timestamp + pid + 8 hex from urandom.
# Optional suffix (e.g. region_id) makes ids easier to grep in logs.
generation_id() {
  local suffix="${1:-}"
  local ts rand
  ts="$(date -u +%Y%m%dT%H%M%S)"
  # Prefer hexdump/od; fall back to $RANDOM$RANDOM if urandom unavailable.
  if rand="$(dd if=/dev/urandom bs=4 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n')"; then
    rand="${rand:0:8}"
  else
    rand="$(printf '%04x%04x' "${RANDOM}" "${RANDOM}")"
  fi
  if [[ -n "$suffix" ]]; then
    # Sanitize suffix to filesystem-safe token.
    suffix="$(printf '%s' "$suffix" | tr -c 'A-Za-z0-9._-' '_')"
    printf '%sZ-%s-%s-%s\n' "$ts" "$$" "$suffix" "$rand"
  else
    printf '%sZ-%s-%s\n' "$ts" "$$" "$rand"
  fi
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

# Reorder region ids on stdin → stdout using sun-order-regions.py when enabled.
# NAVI_BAKE_SUN_ORDER=0 disables. Uses NAVI_BAKE_START_UNIX or "now" for the
# midnight meridian. Bboxes file defaults to ${NAVI_REGIONS_CONF}.bboxes.json.
follow_sun_order_ids() {
  local conf="${1:-$NAVI_REGIONS_CONF}"
  local bbox="${2:-${conf}.bboxes.json}"
  if [[ "${NAVI_BAKE_SUN_ORDER:-1}" == "0" ]]; then
    cat
    return 0
  fi
  if [[ ! -f "$bbox" ]]; then
    log_warn "sun-order: bbox file missing (${bbox}); keeping input order"
    cat
    return 0
  fi
  python3 "${SCRIPT_DIR}/sun-order-regions.py" --bboxes "$bbox"
}

# In-place reorder of a bash array name holding region ids.
order_regions_array_follow_sun() {
  local -n _arr=$1
  local conf="${2:-$NAVI_REGIONS_CONF}"
  local bbox="${3:-${conf}.bboxes.json}"
  [[ ${#_arr[@]} -gt 0 ]] || return 0
  if [[ "${NAVI_BAKE_SUN_ORDER:-1}" == "0" ]]; then
    return 0
  fi
  if [[ ! -f "$bbox" ]]; then
    log_warn "sun-order: bbox file missing (${bbox}); keeping input order"
    return 0
  fi
  local ordered
  mapfile -t ordered < <(printf '%s\n' "${_arr[@]}" | follow_sun_order_ids "$conf" "$bbox")
  _arr=("${ordered[@]}")
}
