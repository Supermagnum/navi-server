#!/usr/bin/env bash
# Build a filtered named-routes .osm.pbf from Geofabrik / covering extracts.
#
# Pipeline: osmium tags-filter (route/network relations) → merge → sort.
# Intentionally does NOT run OPL-based dedupe (that path previously produced
# empty/tiny PBFs). Duplicate object versions in merged extracts are OK:
# annotate-named-routes.py falls back to in-process --bbox export when poly
# clip is empty (bbox_fallback).
#
# Usage:
#   ./scripts/mwm/build-named-routes-pbf.sh
#   OUT=/media/navi/navi-server/data/mwm/inputs/planet-named-routes.osm.pbf \
#     ./scripts/mwm/build-named-routes-pbf.sh
#   # then point NAVI_MWM_NAMED_ROUTES_PBF at OUT (must be >= 1 MiB).
set -euo pipefail

OUT_DIR="${NAVI_NR_BUILD_DIR:-/media/navi/haaken-scratch/osm-named-routes}"
OUT_PBF="${OUT:-${OUT_DIR}/planet-named-routes.osm.pbf}"
WORK_DIR="${NAVI_NR_WORK_DIR:-${OUT_DIR}}"
EXTRACTS_DIR="${NAVI_MWM_EXTRACTS_DIR:-${NAVI_EXTRACTS_DIR:-/media/navi/navi-server/data/scratch/extracts}}"
MIN_BYTES="${NAVI_MWM_NAMED_ROUTES_MIN_BYTES:-1000000}"

mkdir -p "$WORK_DIR"
LOG="${WORK_DIR}/build.log"
exec > >(tee -a "$LOG") 2>&1
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] start named-routes PBF build out=${OUT_PBF}"

FILTER=(
  r/route=pilgrimage
  r/route=hiking
  r/route=foot
  r/route=running
  r/route=fitness_trail
  r/route=bicycle
  r/route=mtb
  r/route=horse
  r/route=ski
  r/route=piste
  r/route=canoe
  r/route=kayak
  r/route=inline_skates
  r/pilgrimage=yes
  r/type=superroute
  # National/regional trail networks (DNT nwn, EuroVelo icn, …)
  r/network=iwn
  r/network=nwn
  r/network=rwn
  r/network=lwn
  r/network=icn
  r/network=ncn
  r/network=rcn
  r/network=lcn
)

command -v osmium >/dev/null || {
  echo "FATAL: osmium not on PATH" >&2
  exit 1
}

SOURCES=()
# Prefer large covering extracts first when present.
for cand in \
  /media/navi/haaken-scratch/comaps-feasibility/inputs/scandinavia-norse.osm.pbf \
  /media/navi/haaken-scratch/comaps-feasibility/inputs/norway-latest.osm.pbf
do
  [[ -f "$cand" ]] && SOURCES+=("$cand")
done
while IFS= read -r f; do
  SOURCES+=("$f")
done < <(ls "${EXTRACTS_DIR}"/*.osm.pbf 2>/dev/null || true)

PARTS=()
i=0
for src in "${SOURCES[@]}"; do
  [[ -f "$src" ]] || continue
  i=$((i + 1))
  part="${WORK_DIR}/part-$(printf '%03d' "$i")-$(basename "$src" .osm.pbf).osm.pbf"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] filter $src -> $part"
  osmium tags-filter -o "$part" -O "$src" "${FILTER[@]}"
  PARTS+=("$part")
done

if [[ ${#PARTS[@]} -eq 0 ]]; then
  echo "FATAL: no source parts (check EXTRACTS_DIR=${EXTRACTS_DIR})" >&2
  exit 1
fi

MERGED="${WORK_DIR}/named-routes-merged.osm.pbf"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] merge ${#PARTS[@]} parts -> $MERGED"
if [[ ${#PARTS[@]} -eq 1 ]]; then
  cp -a "${PARTS[0]}" "$MERGED"
else
  osmium merge -o "$MERGED" -O "${PARTS[@]}"
fi

# Sort only — do NOT dedupe via OPL (that path produced empty/tiny PBFs).
mkdir -p "$(dirname "$OUT_PBF")"
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] sort -> $OUT_PBF"
osmium sort -o "$OUT_PBF" -O "$MERGED"
osmium fileinfo -e "$OUT_PBF" || true

BYTES=$(stat -c%s "$OUT_PBF" 2>/dev/null || echo 0)
if [[ "$BYTES" -lt "$MIN_BYTES" ]]; then
  echo "FATAL: output too small (${BYTES} < ${MIN_BYTES} bytes): $OUT_PBF" >&2
  exit 1
fi
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] DONE $OUT_PBF bytes=${BYTES}"
echo "Wire into bake: NAVI_MWM_NAMED_ROUTES_PBF=${OUT_PBF}"
