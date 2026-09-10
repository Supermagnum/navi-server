#!/usr/bin/env bash
# Isolated tests for verify-published-checksums.sh (no live published tree).
#
# Usage: ./test-verify-published-checksums.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

PASS=0
FAIL=0
assert_eq() {
  local got="$1" want="$2" name="$3"
  if [[ "$got" == "$want" ]]; then
    echo "PASS $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL $name got=${got@Q} want=${want@Q}"
    FAIL=$((FAIL + 1))
  fi
}

assert_contains() {
  local hay="$1" needle="$2" name="$3"
  if grep -Fq -- "$needle" <<<"$hay"; then
    echo "PASS $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL $name missing=${needle@Q}"
    FAIL=$((FAIL + 1))
  fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PACK="$TMP/pack"
mkdir -p "$PACK/scratch/extracts" "$PACK/state/regions" "$PACK/logs" \
  "$PACK/published/packs/europe/andorra/20260904T120000Z" \
  "$PACK/published/packs/europe/andorra/20260903T120000Z"

# --- fixture: good live gen + good prior ---
good_payload="pack-payload-good-v1"
printf '%s' "$good_payload" >"$PACK/published/packs/europe/andorra/20260904T120000Z/graph-car.rkyv"
printf '%s' "$good_payload" >"$PACK/published/packs/europe/andorra/20260903T120000Z/graph-car.rkyv"
digest="$(printf '%s' "$good_payload" | sha256sum | awk '{print $1}')"
printf '%s  graph-car.rkyv\n' "$digest" \
  >"$PACK/published/packs/europe/andorra/20260904T120000Z/checksums.sha256"
printf '%s  graph-car.rkyv\n' "$digest" \
  >"$PACK/published/packs/europe/andorra/20260903T120000Z/checksums.sha256"
# manifests mark gens complete for published_tree.complete_gens
printf '{"schema":1,"generation":"20260904T120000Z","bake_id":"europe_andorra","has_delta_h":false}\n' \
  >"$PACK/published/packs/europe/andorra/20260904T120000Z/manifest.json"
printf '{"schema":1,"generation":"20260903T120000Z","bake_id":"europe_andorra","has_delta_h":false}\n' \
  >"$PACK/published/packs/europe/andorra/20260903T120000Z/manifest.json"

cat >"$PACK/config.env" <<EOF
NAVI_PACK_ROOT=$PACK
NAVI_REGIONS_CONF=$PACK/regions.conf
NAVI_PUBLISHED_DIR=$PACK/published
EOF
printf 'europe_andorra\tgeofabrik:europe/andorra\n' >"$PACK/regions.conf"

run_verify() {
  local out="$1"
  shift
  set +e
  env NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" \
    "${SCRIPT_DIR}/verify-published-checksums.sh" "$@" >"$out" 2>&1
  local rc=$?
  set -e
  echo "$rc"
}

echo "=== live gen only: matching digests ==="
out="$TMP/out_ok.txt"
rc="$(run_verify "$out")"
assert_eq "$rc" "0" "good live gen exit 0"
assert_contains "$(cat "$out")" "CHECKSUM_REVERIFY=PASS" "good live PASS line"
# must not have mutated pack files
assert_eq "$(cat "$PACK/published/packs/europe/andorra/20260904T120000Z/graph-car.rkyv")" \
  "$good_payload" "good path files untouched"

echo "=== --all-complete verifies both gens ==="
out="$TMP/out_all.txt"
rc="$(run_verify "$out" --all-complete)"
assert_eq "$rc" "0" "all-complete exit 0"
assert_contains "$(cat "$out")" "checked=2" "all-complete checked=2"

echo "=== --pack-dir happy path ==="
out="$TMP/out_packdir.txt"
rc="$(run_verify "$out" --pack-dir "$PACK/published/packs/europe/andorra/20260904T120000Z")"
assert_eq "$rc" "0" "pack-dir exit 0"

echo "=== corrupted payload: FAIL, no deletes ==="
printf '%s' "CORRUPTED" >"$PACK/published/packs/europe/andorra/20260904T120000Z/graph-car.rkyv"
before_listing="$(find "$PACK/published" -type f | sort | sha256sum | awk '{print $1}')"
out="$TMP/out_bad.txt"
rc="$(run_verify "$out" --pack-dir "$PACK/published/packs/europe/andorra/20260904T120000Z")"
assert_eq "$rc" "1" "corrupt exit 1"
assert_contains "$(cat "$out")" "CHECKSUM_REVERIFY=FAIL" "corrupt FAIL line"
after_listing="$(find "$PACK/published" -type f | sort | sha256sum | awk '{print $1}')"
assert_eq "$before_listing" "$after_listing" "corrupt path did not delete/mutate tree listing"
# restore for remaining tests
printf '%s' "$good_payload" >"$PACK/published/packs/europe/andorra/20260904T120000Z/graph-car.rkyv"

echo "=== missing checksums.sha256: FAIL ==="
rm -f "$PACK/published/packs/europe/andorra/20260903T120000Z/checksums.sha256"
out="$TMP/out_missing.txt"
rc="$(run_verify "$out" --pack-dir "$PACK/published/packs/europe/andorra/20260903T120000Z")"
assert_eq "$rc" "1" "missing checksums exit 1"
assert_contains "$(cat "$out")" "missing_checksums.sha256" "missing checksums reason"
# file still present
[[ -f "$PACK/published/packs/europe/andorra/20260903T120000Z/graph-car.rkyv" ]]
assert_eq "$?" "0" "missing-checksums path did not delete pack file"

echo "=== .publish_in_progress: FAIL ==="
: >"$PACK/published/packs/europe/andorra/20260904T120000Z/.publish_in_progress"
out="$TMP/out_wip.txt"
rc="$(run_verify "$out" --pack-dir "$PACK/published/packs/europe/andorra/20260904T120000Z")"
assert_eq "$rc" "1" "in-progress exit 1"
assert_contains "$(cat "$out")" "publish_in_progress" "in-progress reason"
rm -f "$PACK/published/packs/europe/andorra/20260904T120000Z/.publish_in_progress"

if [[ "$FAIL" -ne 0 ]]; then
  echo "FAILED ${FAIL} checks (${PASS} passed)" >&2
  exit 1
fi
echo "ALL PASS (${PASS})"
