#!/usr/bin/env bash
# Shared helpers for the MWM pipeline (Nordic pilot).
# shellcheck shell=bash

mwm_repo_root() {
  local here
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
  echo "$here"
}

mwm_load_defaults() {
  local root
  root="$(mwm_repo_root)"
  : "${NAVI_MWM_ENABLED:=0}"
  : "${NAVI_MWM_COMAPS_ROOT:=/var/lib/navi/mwm/comaps}"
  : "${NAVI_MWM_BUILD_DIR:=/var/lib/navi/mwm/omim-build-release}"
  : "${NAVI_MWM_MAPS_BUILD:=/var/lib/navi/mwm/maps_build}"
  : "${NAVI_MWM_HGT_CACHE:=${root}/data/elevation/hgt-mapterhorn}"
  : "${NAVI_MWM_STATE_DIR:=${root}/data/state}"
  : "${NAVI_MWM_LOCK:=${NAVI_MWM_STATE_DIR}/pack-pipeline.lock}"
  : "${NAVI_MWM_PUBLISHED_ROOT:=${root}/data/published/mwm}"
  : "${NAVI_MWM_LEAFSET:=${root}/config/mwm/leafsets/nordic.txt}"
  : "${NAVI_MWM_PIN_JSON:=${root}/config/mwm/comaps-pin.json}"
  : "${NAVI_MWM_REGION_MAP:=${root}/config/mwm/catalog/region_leaf_mapping.json}"
  : "${NAVI_MWM_MEMORY_MAX:=14G}"
  : "${NAVI_MWM_ALLOWED_CPUS:=}"
  : "${NAVI_MWM_THREADS_FEATURES:=4}"
  : "${NAVI_MWM_NODE_STORAGE:=map}"
  : "${NAVI_MWM_GRACE_DAYS:=${NAVI_GRACE_DAYS:-7}}"
  : "${NAVI_MWM_KEEP_RELEASES:=2}"
  : "${NAVI_MWM_AMBIGUOUS_THRESHOLD:=0}"
  : "${NAVI_MWM_MAPTERHORN_PMTILES:=}"
  : "${NAVI_MWM_MAPTERHORN_ATTRIBUTION_URL:=https://download.mapterhorn.com/attribution.json}"
  : "${NAVI_MWM_PLANET_PBF:=}"
  mkdir -p "$NAVI_MWM_STATE_DIR" "$NAVI_MWM_HGT_CACHE"
}

mwm_log() { echo "[mwm $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
mwm_die() { mwm_log "ERROR: $*"; exit 1; }

mwm_pin_field() {
  local key="$1"
  python3 -c "import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])" \
    "$NAVI_MWM_PIN_JSON" "$key"
}

# Acquire exclusive flock on pack-pipeline.lock (shared with weekly .rkyv bake when wired).
# Usage: mwm_with_lock <command...>
mwm_with_lock() {
  mkdir -p "$(dirname "$NAVI_MWM_LOCK")"
  # shellcheck disable=SC2094
  (
    flock -n 9 || {
      echo "[mwm] ERROR: pack-pipeline.lock busy (${NAVI_MWM_LOCK})" >&2
      exit 75
    }
    echo "[mwm $(date -u +%Y-%m-%dT%H:%M:%SZ)] acquired lock ${NAVI_MWM_LOCK}"
    "$@"
  ) 9>"$NAVI_MWM_LOCK"
}

mwm_read_leafset() {
  local f="${1:-$NAVI_MWM_LEAFSET}"
  python3 - "$f" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
for line in p.read_text().splitlines():
    s = line.strip()
    if not s or s.startswith("#"):
        continue
    print(s)
PY
}

mwm_countries_csv() {
  mwm_read_leafset | paste -sd, -
}
