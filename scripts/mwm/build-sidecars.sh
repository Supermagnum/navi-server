#!/usr/bin/env bash
# Build {leaf}.navi-sidecar.v1.zst for every leaf MWM in a version dir.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

VERSION_DIR=""
PBF="${NAVI_MWM_PLANET_PBF:-}"
SKIP_ANNOTATE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version-dir) VERSION_DIR="$2"; shift 2 ;;
    --pbf) PBF="$2"; shift 2 ;;
    --skip-annotate) SKIP_ANNOTATE=1; shift ;;
    *) mwm_die "unknown arg: $1" ;;
  esac
done
[[ -n "$VERSION_DIR" && -d "$VERSION_DIR" ]] || mwm_die "need --version-dir"
TAG="$(mwm_pin_field tag)"
SHA="$(mwm_pin_field sha)"

ROOT="$(mwm_repo_root)"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-${ROOT}/target}"
BIN="${CARGO_TARGET_DIR}/release/navi-mwm-sidecar"
if [[ ! -x "$BIN" ]]; then
  cargo build -p navi-mwm-sidecar --release --manifest-path "${ROOT}/Cargo.toml"
fi
[[ -x "$BIN" ]] || mwm_die "navi-mwm-sidecar binary missing at ${BIN}"

ATTR_JSON="${VERSION_DIR}/attribution.json"
if [[ ! -f "$ATTR_JSON" ]]; then
  if [[ -f "${VERSION_DIR}/attribution-mapterhorn.json" ]]; then
    cp -a "${VERSION_DIR}/attribution-mapterhorn.json" "$ATTR_JSON"
  else
    python3 - <<PY
import json
from pathlib import Path
Path("${ATTR_JSON}").write_text(json.dumps({
  "label": "Mapterhorn",
  "note": "Per-release elevation attribution; never generic elevation data",
  "generator_tag": "${TAG}",
  "generator_sha": "${SHA}",
}, indent=2) + "\n")
PY
  fi
fi

WORKDIR="${VERSION_DIR}/navi-sidecar-work"
mkdir -p "$WORKDIR"

while IFS= read -r leaf; do
  [[ -n "$leaf" ]] || continue
  MWM="${VERSION_DIR}/${leaf}.mwm"
  [[ -f "$MWM" ]] || { mwm_log "skip missing mwm ${leaf}"; continue; }
  OSM2FT="${VERSION_DIR}/${leaf}.mwm.osm2ft"
  WJSON="${WORKDIR}/${leaf}.wetland.jsonl"
  BJSON="${WORKDIR}/${leaf}.boardwalk.jsonl"
  OUT="${VERSION_DIR}/${leaf}.navi-sidecar.v1.zst"

  if [[ "$SKIP_ANNOTATE" == "1" ]]; then
    : >"$WJSON"
    : >"$BJSON"
  else
    [[ -n "$PBF" && -f "$PBF" ]] || mwm_die "PBF required for annotate"
    [[ -f "$OSM2FT" ]] || mwm_die "missing osm2ft for ${leaf}: ${OSM2FT}"
    BORDER="${NAVI_MWM_COMAPS_ROOT}/data/borders/${leaf}.poly"
    python3 "${SCRIPT_DIR}/annotate-wetland-edges.py" \
      --pbf "$PBF" \
      --osm2ft "$OSM2FT" \
      --border-poly "$BORDER" \
      --out-wetland "$WJSON" \
      --out-boardwalk "$BJSON"
  fi

  "$BIN" bake \
    --mwm "$MWM" \
    --wetland-jsonl "$WJSON" \
    --boardwalk-jsonl "$BJSON" \
    --attribution "$ATTR_JSON" \
    --generator-tag "$TAG" \
    --generator-sha "$SHA" \
    --ambiguous-threshold "${NAVI_MWM_AMBIGUOUS_THRESHOLD}" \
    --out "$OUT"
  mwm_log "sidecar ${leaf} -> ${OUT}"
done < <(mwm_read_leafset)

mwm_log "build-sidecars OK"
