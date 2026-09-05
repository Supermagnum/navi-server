#!/usr/bin/env bash
# Fetch step: pull regional .osm.pbf extracts into scratch, verify checksums
# when published, skip unchanged regions via ETag / Last-Modified.
#
# Usage:
#   ./fetch-extracts.sh                  # all regions in regions.conf
#   ./fetch-extracts.sh hedmark          # single region id
#   ./fetch-extracts.sh --force hedmark  # ignore conditional headers

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config
require_cmd curl md5sum

FORCE=0
FILTER_IDS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *) FILTER_IDS+=("$1"); shift ;;
  esac
done

fetch_one() {
  local region_id="$1"
  local src="$2"
  local url pbf state_dir meta etag_file lm_file tmp md5_url md5_file expected actual
  url="$(region_pbf_url "$src")"
  pbf="$(region_pbf_path "$region_id")"
  state_dir="$(region_state_dir "$region_id")"
  mkdir -p "$state_dir"
  etag_file="${state_dir}/etag"
  lm_file="${state_dir}/last-modified"
  meta="${state_dir}/fetch.json"
  tmp="${pbf}.partial"

  log_info "fetch region=${region_id} url=${url}"

  local curl_args=(
    -fL
    --connect-timeout "${NAVI_HTTP_TIMEOUT_SECS}"
    --retry 3
    --retry-delay 5
    -o "$tmp"
    -w '%{http_code}|%{size_download}|%{time_total}'
    -D "${state_dir}/headers.raw"
  )

  if [[ "$FORCE" -eq 0 && -f "$pbf" ]]; then
    if [[ -f "$etag_file" ]]; then
      curl_args+=(-H "If-None-Match: $(cat "$etag_file")")
    fi
    if [[ -f "$lm_file" ]]; then
      curl_args+=(-H "If-Modified-Since: $(cat "$lm_file")")
    fi
  fi

  local result http_code
  set +e
  result="$(curl "${curl_args[@]}" "$url")"
  local rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    rm -f "$tmp"
    # curl -f treats 304 as failure; detect from headers if present
    if [[ -f "${state_dir}/headers.raw" ]] && grep -qiE '^HTTP/.* 304' "${state_dir}/headers.raw"; then
      log_info "skip unchanged region=${region_id} (HTTP 304)"
      printf '{"region_id":"%s","status":"unchanged","url":"%s"}\n' \
        "$region_id" "$url" >"$meta"
      return 0
    fi
    die "download failed region=${region_id} curl_rc=${rc}"
  fi

  http_code="${result%%|*}"
  if [[ "$http_code" == "304" ]]; then
    rm -f "$tmp"
    log_info "skip unchanged region=${region_id} (HTTP 304)"
    printf '{"region_id":"%s","status":"unchanged","url":"%s"}\n' \
      "$region_id" "$url" >"$meta"
    return 0
  fi

  if [[ ! -s "$tmp" ]]; then
    rm -f "$tmp"
    die "empty download region=${region_id}"
  fi

  # Persist validators for next week.
  awk 'BEGIN{IGNORECASE=1} /^etag:/{sub(/\r$/,""); sub(/^[^:]+:[[:space:]]*/,""); print; exit}' \
    "${state_dir}/headers.raw" >"${etag_file}.new" || true
  if [[ -s "${etag_file}.new" ]]; then
    mv "${etag_file}.new" "$etag_file"
  else
    rm -f "${etag_file}.new"
  fi
  awk 'BEGIN{IGNORECASE=1} /^last-modified:/{sub(/\r$/,""); sub(/^[^:]+:[[:space:]]*/,""); print; exit}' \
    "${state_dir}/headers.raw" >"${lm_file}.new" || true
  if [[ -s "${lm_file}.new" ]]; then
    mv "${lm_file}.new" "$lm_file"
  else
    rm -f "${lm_file}.new"
  fi

  mv "$tmp" "$pbf"
  log_info "wrote ${pbf} bytes=$(file_size_bytes "$pbf")"

  # Checksum when the provider publishes one.
  if md5_url="$(region_md5_url "$src")"; then
    md5_file="${pbf}.md5"
    log_info "fetch checksum ${md5_url}"
    if curl -fL --connect-timeout "${NAVI_HTTP_TIMEOUT_SECS}" -o "$md5_file" "$md5_url"; then
      expected="$(awk '{print $1; exit}' "$md5_file")"
      actual="$(md5sum "$pbf" | awk '{print $1}')"
      if [[ "$expected" != "$actual" ]]; then
        die "CHECKSUM MISMATCH region=${region_id} expected=${expected} actual=${actual}"
      fi
      log_info "checksum OK region=${region_id} md5=${actual}"
    else
      log_warn "checksum URL failed for region=${region_id}; leaving PBF but flagging"
      printf '{"region_id":"%s","status":"downloaded","checksum":"unavailable","url":"%s","bytes":%s}\n' \
        "$region_id" "$url" "$(file_size_bytes "$pbf")" >"$meta"
      return 0
    fi
  else
    log_warn "no published checksum for region=${region_id} (source=$(region_source_kind "$src")); verifying non-empty PBF only"
  fi

  printf '{"region_id":"%s","status":"downloaded","url":"%s","bytes":%s,"path":"%s"}\n' \
    "$region_id" "$url" "$(file_size_bytes "$pbf")" "$pbf" >"$meta"
}

matched=0
# Build work list, then follow-the-sun order when processing the full set.
WORK=()
while IFS=$'\t' read -r region_id src; do
  if [[ ${#FILTER_IDS[@]} -gt 0 ]]; then
    keep=0
    for f in "${FILTER_IDS[@]}"; do
      [[ "$f" == "$region_id" ]] && keep=1
    done
    [[ "$keep" -eq 1 ]] || continue
  fi
  WORK+=("${region_id}"$'\t'"${src}")
done < <(list_regions)

if [[ ${#FILTER_IDS[@]} -eq 0 ]]; then
  mapfile -t ORDERED_IDS < <(printf '%s\n' "${WORK[@]}" | awk -F'\t' '{print $1}' | follow_sun_order_ids)
  declare -A SRC_BY_ID=()
  for row in "${WORK[@]}"; do
    SRC_BY_ID["${row%%$'\t'*}"]="${row#*$'\t'}"
  done
  WORK=()
  for region_id in "${ORDERED_IDS[@]}"; do
    if [[ -z "$region_id" ]]; then
      die "sun-order produced an empty region id (stdout pollution?)"
    fi
    if [[ ! -v SRC_BY_ID[$region_id] ]]; then
      die "sun-order produced unknown region id=${region_id@Q} (not in regions.conf; often log noise captured into the id list)"
    fi
    WORK+=("${region_id}"$'\t'"${SRC_BY_ID[$region_id]}")
  done
  log_info "fetch sun_order=${NAVI_BAKE_SUN_ORDER:-1} regions=${#WORK[@]}"
fi

for row in "${WORK[@]+"${WORK[@]}"}"; do
  region_id="${row%%$'\t'*}"
  src="${row#*$'\t'}"
  matched=1
  fetch_one "$region_id" "$src"
done

if [[ "$matched" -eq 0 ]]; then
  die "no matching regions (filter=${FILTER_IDS[*]:-none}; conf=${NAVI_REGIONS_CONF})"
fi

log_info "fetch step complete"
