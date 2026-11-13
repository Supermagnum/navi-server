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
  # Default: CoMaps official planet release + Navi sidecars only.
  # Set NAVI_MWM_MODE=generate to keep the Nordic generator path for testing.
  : "${NAVI_MWM_MODE:=comaps-release}"
  : "${NAVI_MWM_COMAPS_ROOT:=/var/lib/navi/mwm/comaps}"
  : "${NAVI_MWM_BUILD_DIR:=/var/lib/navi/mwm/omim-build-release}"
  : "${NAVI_MWM_MAPS_BUILD:=/var/lib/navi/mwm/maps_build}"
  : "${NAVI_MWM_HGT_CACHE:=${root}/data/elevation/hgt-mapterhorn}"
  # Persistent across CoMaps releases (Mapterhorn tiles are release-independent).
  : "${NAVI_MWM_MAPTERHORN_CACHE:=${root}/data/elevation/mapterhorn-cache}"
  # Default 48 GiB (pre-world); after first world run raise to measured+20% via env.
  : "${NAVI_MWM_MAPTERHORN_CACHE_MAX_BYTES:=51539607552}"
  : "${NAVI_MWM_MAPTERHORN_ZOOM:=12}"
  : "${NAVI_MWM_ELEV_THRESHOLD_M:=2}"
  : "${NAVI_MWM_STATE_DIR:=${root}/data/state}"
  : "${NAVI_MWM_LOCK:=${NAVI_MWM_STATE_DIR}/pack-pipeline.lock}"
  : "${NAVI_MWM_PUBLISHED_ROOT:=${root}/data/published/mwm}"
  : "${NAVI_MWM_LEAFSET:=${root}/config/mwm/leafsets/nordic.txt}"
  : "${NAVI_MWM_PIN_JSON:=${root}/config/mwm/comaps-pin.json}"
  : "${NAVI_MWM_MEASURED_PEAKS:=${root}/config/mwm/measured-peaks.json}"
  : "${NAVI_MWM_REGION_MAP:=${root}/config/mwm/catalog/region_leaf_mapping.json}"
  : "${NAVI_MWM_METASERVER:=https://cdn-us-1.comaps.app}"
  : "${NAVI_MWM_MAP_SERIES:=2026.10.27}"
  : "${NAVI_MWM_COMAPS_VERSION:=}"
  : "${NAVI_MWM_DOWNLOAD_DIR:=}"
  : "${NAVI_MWM_DOWNLOAD_CONCURRENCY:=2}"
  # Process concurrency: 0 → derive from free RAM + per-stage peaks (see leaf_scheduler.py).
  : "${NAVI_MWM_PROCESS_CONCURRENCY:=0}"
  # Optional single-value override; empty uses stage_peaks_gib from measured-peaks.json.
  : "${NAVI_MWM_PER_LEAF_GIB:=}"
  : "${NAVI_MWM_RAM_HEADROOM_GIB:=16}"
  : "${NAVI_MWM_ANNOTATE_CONCURRENCY:=2}"
  : "${NAVI_MWM_TILE_FETCH_CONCURRENCY:=16}"
  : "${NAVI_MWM_TILE_MEM_CACHE:=4096}"
  : "${NAVI_MWM_PREFETCH_DISK_BUDGET_GIB:=20}"
  : "${NAVI_MWM_SCHEDULE_GUARD:=1}"
  : "${NAVI_MWM_WEEKLY_BAKE_WEEKDAY:=0}"
  : "${NAVI_MWM_WEEKLY_BAKE_UTC:=00:00}"
  : "${NAVI_MWM_PAUSE_BEFORE_HOURS:=6}"
  : "${NAVI_MWM_PAUSE_AFTER_HOURS:=6}"
  # Nordic measured peak ~19–20 GiB MaxRSS; 14G cgroup OOMs Features.
  : "${NAVI_MWM_MEMORY_MAX:=24G}"
  : "${NAVI_MWM_ALLOWED_CPUS:=}"
  # Nordic defaults: NODE_STORAGE=map (raw measured worse on Nordic), cap threads.
  : "${NAVI_MWM_THREADS_FEATURES:=4}"
  : "${NAVI_MWM_THREADS_COUNT:=4}"
  : "${NAVI_MWM_NODE_STORAGE:=map}"
  : "${NAVI_MWM_CACHE_PATH:=}"
  : "${NAVI_MWM_PROFILE:=}"
  : "${NAVI_MWM_GENERATOR_SKIP:=Coastline}"
  : "${NAVI_MWM_GRACE_DAYS:=${NAVI_GRACE_DAYS:-7}}"
  : "${NAVI_MWM_KEEP_RELEASES:=2}"
  : "${NAVI_MWM_AMBIGUOUS_THRESHOLD:=0}"
  : "${NAVI_MWM_MAPTERHORN_PMTILES:=}"
  : "${NAVI_MWM_MAPTERHORN_ATTRIBUTION_URL:=https://download.mapterhorn.com/attribution.json}"
  : "${NAVI_MWM_PLANET_PBF:=}"
  # comaps-release wetland bind reads the MWM itself; PBF only for generate mode.
  : "${NAVI_MWM_ALL_LEAVES:=0}"
  : "${NAVI_MWM_HTTP_USER_AGENT:=navi-server-mwm/1.0 (+https://github.com/Supermagnum/navi-server; contact=mwm-pipeline)}"
  mkdir -p "$NAVI_MWM_STATE_DIR" "$NAVI_MWM_HGT_CACHE" "$NAVI_MWM_MAPTERHORN_CACHE"
}

# Apply config/mwm/profiles/{nordic,planet}.json when NAVI_MWM_PROFILE is set,
# or infer from leafset basename. Overwrites defaults. Set NAVI_MWM_NO_PROFILE=1
# to keep explicit env / defaults only.
mwm_apply_profile_from_leafset() {
  local root profile_name profile_path
  [[ "${NAVI_MWM_NO_PROFILE:-0}" == "1" ]] && return 0
  root="$(mwm_repo_root)"
  if [[ -n "${NAVI_MWM_PROFILE}" ]]; then
    profile_name="${NAVI_MWM_PROFILE}"
  else
    profile_name="$(basename "${NAVI_MWM_LEAFSET:-nordic.txt}" .txt)"
  fi
  profile_path="${root}/config/mwm/profiles/${profile_name}.json"
  [[ -f "$profile_path" ]] || return 0
  eval "$(python3 - "$profile_path" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
mapping = {
    "node_storage": "NAVI_MWM_NODE_STORAGE",
    "threads_features": "NAVI_MWM_THREADS_FEATURES",
    "threads_count": "NAVI_MWM_THREADS_COUNT",
    "memory_max_hint": "NAVI_MWM_MEMORY_MAX",
    "cache_path": "NAVI_MWM_CACHE_PATH",
}
for k, env in mapping.items():
    if k not in p:
        continue
    v = p[k]
    if v is None or v == "":
        continue
    print(f"export {env}={json.dumps(str(v))}")
print(f"export NAVI_MWM_PROFILE_LOADED={json.dumps(sys.argv[1])}")
PY
)"
  mwm_log "profile loaded=${NAVI_MWM_PROFILE_LOADED:-none} node_storage=${NAVI_MWM_NODE_STORAGE} threads_features=${NAVI_MWM_THREADS_FEATURES} threads_count=${NAVI_MWM_THREADS_COUNT}"
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
