#!/usr/bin/env bash
# Fixture smoke for migrate-mwm.sh (no live data; no sudo).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

SRC="$TMP/src"
DST="$TMP/dst"
mkdir -p "$SRC/data/published/mwm/261105" "$SRC/config/mwm" "$SRC/data/state" \
  "$SRC/data/published/packs/europe/x/g"
printf 'owned-by=navi-mwm-pipeline\n' >"$SRC/data/published/mwm/.navi-mwm-owned"
echo sidecar >"$SRC/data/published/mwm/261105/Norway_Oslo.navi-sidecar.v1.zst"
DIGEST="$(sha256sum "$SRC/data/published/mwm/261105/Norway_Oslo.navi-sidecar.v1.zst" | awk '{print $1}')"
echo "${DIGEST}  Norway_Oslo.navi-sidecar.v1.zst" >"$SRC/data/published/mwm/261105/checksums.sha256"
echo '{"version":"261105"}' >"$SRC/data/published/mwm/current.json"
echo '{}' >"$SRC/config/mwm/measured-peaks.json"
echo rkyv >"$SRC/data/published/current.json"
echo pack >"$SRC/data/published/packs/europe/x/g/a.rkyv"
: >"$SRC/data/state/pack-pipeline.lock"

echo "== dry-run =="
out="$("${SCRIPT_DIR}/migrate-mwm.sh" --source "$SRC" --dest "$DST" 2>&1)"
echo "$out" | grep -q 'forbidden_path_count=0'
echo "$out" | grep -q 'dry-run only'
echo "$out" | grep -qv 'packs/'
echo "$out" | grep -qv '\.rkyv'

echo "== apply =="
"${SCRIPT_DIR}/migrate-mwm.sh" --source "$SRC" --dest "$DST" --apply
[[ -f "$DST/data/published/mwm/current.json" ]]
[[ ! -e "$DST/data/published/packs" ]]
[[ ! -e "$DST/data/published/current.json" ]]
cmp -s "$SRC/data/published/mwm/current.json" "$DST/data/published/mwm/current.json"

echo "== idempotent re-apply =="
"${SCRIPT_DIR}/migrate-mwm.sh" --source "$SRC" --dest "$DST" --apply >/dev/null

echo "== negative: path escape via --mapterhorn-cache =="
if "${SCRIPT_DIR}/migrate-mwm.sh" --source "$SRC" --dest "$DST/bad" \
    --with-mapterhorn-cache --mapterhorn-cache /tmp --apply 2>/dev/null; then
  echo "FAIL: expected abort for bad mapterhorn path"
  exit 1
fi
echo "PASS negative mapterhorn"

echo "== negative: pretend packs as mwm (missing marker) =="
BAD="$TMP/badsrc"
mkdir -p "$BAD/data/published/mwm"
echo x >"$BAD/data/published/mwm/current.json"
if "${SCRIPT_DIR}/migrate-mwm.sh" --source "$BAD" --dest "$DST/bad2" --apply 2>/dev/null; then
  echo "FAIL: expected missing marker abort"
  exit 1
fi
echo "PASS negative marker"

echo "ALL test-migrate-mwm.sh checks passed"
