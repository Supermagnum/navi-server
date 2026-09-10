#!/usr/bin/env bash
# Isolated tests for publish-packs.sh pre-prune checksum gate.
# Does not touch the live published tree.
#
# Usage: ./test-publish-preprune-checksum.sh

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

assert_exists() {
  local path="$1" name="$2"
  if [[ -e "$path" ]]; then
    echo "PASS $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL $name missing=${path@Q}"
    FAIL=$((FAIL + 1))
  fi
}

assert_missing() {
  local path="$1" name="$2"
  if [[ ! -e "$path" ]]; then
    echo "PASS $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL $name still_present=${path@Q}"
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

write_published_gen() {
  local root="$1" gen="$2" payload="$3"
  local dir="$root/published/packs/europe/andorra/${gen}"
  mkdir -p "$dir"
  printf '%s' "$payload" >"${dir}/graph-car.rkyv"
  local digest
  digest="$(printf '%s' "$payload" | sha256sum | awk '{print $1}')"
  printf '%s  graph-car.rkyv\n' "$digest" >"${dir}/checksums.sha256"
  printf '{"schema":1,"generation":"%s","bake_id":"europe_andorra","region_id":"europe/andorra","has_delta_h":false,"files":{"graph-car.rkyv":{"sha256":"%s","bytes":%s}}}\n' \
    "$gen" "$digest" "$(printf '%s' "$payload" | wc -c)" >"${dir}/manifest.json"
}

setup_pack_root() {
  local pack="$1"
  mkdir -p "$pack/scratch/convert/europe_andorra" \
    "$pack/scratch/extracts" "$pack/state/regions" "$pack/logs" \
    "$pack/staging" "$pack/generations" "$pack/published/packs"
  # Convert output for publish assemble.
  printf 'convert-payload-v2' >"$pack/scratch/convert/europe_andorra/graph-car.rkyv"
  printf '{"stem":"europe_andorra-latest","has_delta_h":false}\n' \
    >"$pack/scratch/convert/europe_andorra/europe_andorra-latest.navi-manifest.json"
  cat >"$pack/config.env" <<EOF
NAVI_PACK_ROOT=$pack
NAVI_REGIONS_CONF=$pack/regions.conf
NAVI_PUBLISHED_DIR=$pack/published
NAVI_PUBLISHED_KEEP_PER_REGION=0
NAVI_BAKE_DELTA_H=0
NAVI_BAKE_TOWN_ROUTES=0
EOF
  printf 'europe_andorra\tgeofabrik:europe/andorra\n' >"$pack/regions.conf"
}

run_publish() {
  local pack="$1" out="$2"
  set +e
  env NAVI_PACK_CONFIG="$pack/config.env" NAVI_PACK_ROOT="$pack" \
    NAVI_PUBLISHED_KEEP_PER_REGION=0 \
    "${SCRIPT_DIR}/publish-packs.sh" --skip-validate --region europe_andorra \
    >"$out" 2>&1
  local rc=$?
  set -e
  echo "$rc"
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "=== corrupt live: publish aborts before live swap, priors not pruned ==="
PACK="$TMP/corrupt"
setup_pack_root "$PACK"
# Older + live; live checksum deliberately wrong vs payload.
write_published_gen "$PACK" "20260903T120000Z" "prior-good"
write_published_gen "$PACK" "20260904T120000Z" "live-good"
printf 'CORRUPTED-LIVE' >"$PACK/published/packs/europe/andorra/20260904T120000Z/graph-car.rkyv"
# Internal live symlink points at a prior generations tree (not HTTP packs).
mkdir -p "$PACK/generations/20260901T000000Z-old"
ln -sfn "$PACK/generations/20260901T000000Z-old" "$PACK/live"
live_before="$(readlink -f "$PACK/live")"
listing_before="$(find "$PACK/published/packs/europe/andorra" -type f | sort)"

out="$TMP/out_corrupt.txt"
rc="$(run_publish "$PACK" "$out")"
assert_eq "$rc" "1" "corrupt live publish exit 1"
assert_contains "$(cat "$out")" "CHECKSUM_REVERIFY=FAIL" "corrupt live FAIL signal"
assert_contains "$(cat "$out")" "pre-swap-live" "corrupt live stage=pre-swap-live"
live_after="$(readlink -f "$PACK/live" 2>/dev/null || true)"
assert_eq "$live_after" "$live_before" "corrupt: live symlink unchanged"
assert_eq "$(test -f "$PACK/CURRENT_GENERATION" && echo yes || echo no)" "no" \
  "corrupt: CURRENT_GENERATION not written"
# Staging may exist; generations must not have gained the new publish id.
assert_eq "$(find "$PACK/generations" -mindepth 1 -maxdepth 1 -type d ! -name '20260901T000000Z-old' | wc -l)" \
  "0" "corrupt: staging not promoted to generations/"
assert_exists "$PACK/published/packs/europe/andorra/20260903T120000Z/graph-car.rkyv" \
  "corrupt: prior gen file retained"
assert_exists "$PACK/published/packs/europe/andorra/20260904T120000Z/graph-car.rkyv" \
  "corrupt: live gen file retained"
listing_after="$(find "$PACK/published/packs/europe/andorra" -type f | sort)"
assert_eq "$listing_before" "$listing_after" "corrupt: published file listing unchanged"
gen_count="$(find "$PACK/published/packs/europe/andorra" -mindepth 1 -maxdepth 1 -type d | wc -l)"
assert_eq "$gen_count" "2" "corrupt: no new published gen dir"

echo "=== healthy live: publish OK, keep_prior=0 prunes old live ==="
PACK="$TMP/healthy"
setup_pack_root "$PACK"
write_published_gen "$PACK" "20260903T120000Z" "prior-good"
write_published_gen "$PACK" "20260904T120000Z" "live-good"
mkdir -p "$PACK/generations/20260901T000000Z-old"
ln -sfn "$PACK/generations/20260901T000000Z-old" "$PACK/live"
out="$TMP/out_ok.txt"
rc="$(run_publish "$PACK" "$out")"
assert_eq "$rc" "0" "healthy publish exit 0"
assert_contains "$(cat "$out")" "pre-swap-live" "healthy ran pre-swap-live verify"
assert_contains "$(cat "$out")" "post-write-new" "healthy ran post-write-new verify"
assert_contains "$(cat "$out")" "CHECKSUM_REVERIFY=PASS" "healthy PASS signal"
live_ok="$(readlink -f "$PACK/live")"
[[ "$live_ok" != "$PACK/generations/20260901T000000Z-old" ]]
assert_eq "$?" "0" "healthy: live symlink moved off prior generation"
assert_missing "$PACK/published/packs/europe/andorra/20260903T120000Z" \
  "healthy: oldest pruned (keep_prior=0)"
assert_missing "$PACK/published/packs/europe/andorra/20260904T120000Z" \
  "healthy: previous live pruned (keep_prior=0)"
new_count="$(find "$PACK/published/packs/europe/andorra" -mindepth 1 -maxdepth 1 -type d | wc -l)"
assert_eq "$new_count" "1" "healthy: exactly one published gen remains"
new_dir="$(find "$PACK/published/packs/europe/andorra" -mindepth 1 -maxdepth 1 -type d)"
assert_exists "${new_dir}/checksums.sha256" "healthy: new gen has checksums.sha256"

if [[ "$FAIL" -ne 0 ]]; then
  echo "FAILED ${FAIL} checks (${PASS} passed)" >&2
  echo "---- corrupt log ----" >&2
  cat "$TMP/out_corrupt.txt" >&2 || true
  echo "---- healthy log ----" >&2
  cat "$TMP/out_ok.txt" >&2 || true
  exit 1
fi
echo "ALL PASS (${PASS})"
