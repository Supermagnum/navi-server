#!/usr/bin/env bash
# Held-PBF retention policy: max size + total budget (prefer smaller files).
# Isolated mktemp pack root — does not touch the live published tree.
#
# Usage: ./test-held-pbf-budget.sh

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
EX="$PACK/scratch/extracts"
mkdir -p "$EX" "$PACK/scratch/convert" "$PACK/state" \
  "$PACK/logs" "$PACK/staging" "$PACK/generations" "$PACK/published/packs" \
  "$PACK/live_gen"
ln -sfn "$PACK/live_gen" "$PACK/live"
ln -sfn "$PACK/live_gen" "$PACK/previous"
mkdir -p "$PACK/generations/20260901T000000Z" "$PACK/generations/20260902T000000Z"
touch "$PACK/generations/20260901T000000Z/.keep" "$PACK/generations/20260902T000000Z/.keep"

# Three held PBFs: small / medium / oversize (relative to test knobs).
# sizes: tiny=100 bytes, mid=2000 bytes, huge=5000 bytes
printf 'S%.0s' {1..100} >"$EX/europe_andorra-latest.osm.pbf"
printf 'M%.0s' {1..2000} >"$EX/africa_mali-latest.osm.pbf"
printf 'H%.0s' {1..5000} >"$EX/europe_dach-latest.osm.pbf"
printf 'md5-andorra\n' >"$EX/europe_andorra-latest.osm.pbf.md5"
printf 'poly\n' >"$EX/europe_dach.poly"
# In-flight temp must never be deleted by budget policy.
printf 'partial-in-flight' >"$EX/europe_dach-latest.osm.pbf.incremental.partial.osm.pbf"

cat >"$PACK/config.env" <<EOF
NAVI_PACK_ROOT=$PACK
NAVI_REGIONS_CONF=$PACK/regions.conf
NAVI_KEEP_GENERATIONS=2
NAVI_BAKE_DELTA_H=0
NAVI_HELD_PBF_MAX_MIB=0
NAVI_HELD_PBF_BUDGET_GIB=0
EOF
printf 'europe_andorra\tgeofabrik:europe/andorra\n' >"$PACK/regions.conf"

echo "=== unlimited (budget=0 max=0): all held PBFs survive --no-extracts ==="
set +e
NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" \
  "${SCRIPT_DIR}/cleanup.sh" --skip-quota-gate --no-extracts >"$TMP/out_unlimited.txt" 2>&1
rc=$?
set -e
assert_eq "$rc" "0" "unlimited cleanup exit 0"
assert_eq "$(test -f "$EX/europe_andorra-latest.osm.pbf" && echo yes || echo no)" "yes" "tiny kept (unlimited)"
assert_eq "$(test -f "$EX/africa_mali-latest.osm.pbf" && echo yes || echo no)" "yes" "mid kept (unlimited)"
assert_eq "$(test -f "$EX/europe_dach-latest.osm.pbf" && echo yes || echo no)" "yes" "huge kept (unlimited)"
assert_eq "$(test -f "$EX/europe_dach-latest.osm.pbf.incremental.partial.osm.pbf" && echo yes || echo no)" "yes" \
  "partial temp kept (unlimited)"
assert_contains "$(cat "$TMP/out_unlimited.txt")" "held-pbf budget policy disabled" \
  "logs budget disabled"

echo "=== max-size drops oversize; companions removed; partials untouched ==="
# Recreate payloads (cleanup may have left them).
printf 'S%.0s' {1..100} >"$EX/europe_andorra-latest.osm.pbf"
printf 'M%.0s' {1..2000} >"$EX/africa_mali-latest.osm.pbf"
printf 'H%.0s' {1..5000} >"$EX/europe_dach-latest.osm.pbf"
printf 'md5-dach\n' >"$EX/europe_dach-latest.osm.pbf.md5"
printf 'poly\n' >"$EX/europe_dach.poly"
printf 'partial-in-flight' >"$EX/europe_dach-latest.osm.pbf.incremental.partial.osm.pbf"

# 1 MiB max with byte-sized fixtures: use MAX_MIB=0 for this and test via
# a tiny max by setting env to force drop of files > 3000 bytes through
# budget instead. For max-size: write a config that uses MAX_MIB with a
# dd-made file over 1 MiB would be heavy; instead unit-test max via
# NAVI_HELD_PBF_MAX_MIB=1 and a 1.5 MiB sparse-ish file.
dd if=/dev/zero of="$EX/europe_dach-latest.osm.pbf" bs=1024 count=1500 status=none
printf 'poly\n' >"$EX/europe_dach.poly"
printf 'md5-dach\n' >"$EX/europe_dach-latest.osm.pbf.md5"

cat >"$PACK/config.env" <<EOF
NAVI_PACK_ROOT=$PACK
NAVI_REGIONS_CONF=$PACK/regions.conf
NAVI_KEEP_GENERATIONS=2
NAVI_BAKE_DELTA_H=0
NAVI_HELD_PBF_MAX_MIB=1
NAVI_HELD_PBF_BUDGET_GIB=0
EOF

set +e
NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" \
  "${SCRIPT_DIR}/cleanup.sh" --skip-quota-gate --no-extracts >"$TMP/out_max.txt" 2>&1
rc=$?
set -e
assert_eq "$rc" "0" "max-size cleanup exit 0"
assert_eq "$(test -f "$EX/europe_dach-latest.osm.pbf" && echo yes || echo no)" "no" \
  "oversize PBF dropped by MAX_MIB=1"
assert_eq "$(test -f "$EX/europe_dach-latest.osm.pbf.md5" && echo yes || echo no)" "no" \
  "oversize companion md5 removed"
assert_eq "$(test -f "$EX/europe_dach.poly" && echo yes || echo no)" "no" \
  "oversize companion poly removed"
assert_eq "$(test -f "$EX/europe_andorra-latest.osm.pbf" && echo yes || echo no)" "yes" \
  "tiny kept under max"
assert_eq "$(test -f "$EX/europe_dach-latest.osm.pbf.incremental.partial.osm.pbf" && echo yes || echo no)" "yes" \
  "partial temp not deleted by max policy"
assert_contains "$(cat "$TMP/out_max.txt")" "over_max_mib=1" "logs over_max_mib drop"

echo "=== budget keeps smaller files; drops largest first ==="
# Fresh set under a small byte budget expressed in GiB is awkward for fixtures.
# Use BUDGET_GIB=1 with three files totaling >1 GiB... too heavy.
# Instead: monkey-patch via a 0-byte-budget isn't allowed (0=unlimited).
# Create files of 400KiB + 400KiB + 400KiB and budget 1 MiB ≈ need GiB.
# Practical approach: set NAVI_HELD_PBF_BUDGET_GIB via computing — we can't
# set sub-GiB budgets with the GiB knob. Add test using 1 GiB budget and
# files that sum over 1 GiB would need ~1GB disk — acceptable for a test
# on this host? Prefer a unit-sized approach: temporarily use env override
# with a helper that the policy reads... Keep GiB as shipped; for the test,
# create one 600MiB sparse file + one tiny file with budget 1 GiB — sparse
# files report logical size via stat -c %s which is what we use. Sparse
# truncate is fine and uses little disk.
rm -f "$EX"/*-latest.osm.pbf "$EX"/*.md5 "$EX"/*.poly
truncate -s $((600 * 1024 * 1024)) "$EX/europe_dach-latest.osm.pbf"
truncate -s $((600 * 1024 * 1024)) "$EX/north_america_us-latest.osm.pbf"
printf 'tiny\n' >"$EX/europe_andorra-latest.osm.pbf"
printf 'partial-in-flight' >"$EX/europe_dach-latest.osm.pbf.incremental.partial.osm.pbf"
printf 'poly\n' >"$EX/europe_dach.poly"

cat >"$PACK/config.env" <<EOF
NAVI_PACK_ROOT=$PACK
NAVI_REGIONS_CONF=$PACK/regions.conf
NAVI_KEEP_GENERATIONS=2
NAVI_BAKE_DELTA_H=0
NAVI_HELD_PBF_MAX_MIB=0
NAVI_HELD_PBF_BUDGET_GIB=1
EOF

set +e
NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" \
  "${SCRIPT_DIR}/cleanup.sh" --skip-quota-gate --no-extracts >"$TMP/out_budget.txt" 2>&1
rc=$?
set -e
assert_eq "$rc" "0" "budget cleanup exit 0"
# 600+600+tiny > 1 GiB → drop largest until under 1 GiB. Both 600MiB files
# are equal-ish; sort -nr is stable enough that both oversize get dropped
# until only tiny remains (600+600=1200MiB > 1GiB; after one drop 600+tiny
# still < 1GiB? 600MiB < 1024MiB so ONE of the large files may survive).
# 600 MiB + tiny < 1 GiB → keep one large + tiny; drop the other large.
_kept_large=0
[[ -f "$EX/europe_dach-latest.osm.pbf" ]] && _kept_large=$((_kept_large + 1))
[[ -f "$EX/north_america_us-latest.osm.pbf" ]] && _kept_large=$((_kept_large + 1))
assert_eq "$_kept_large" "1" "exactly one large PBF kept under 1 GiB budget"
assert_eq "$(test -f "$EX/europe_andorra-latest.osm.pbf" && echo yes || echo no)" "yes" \
  "tiny always kept (prefer small)"
assert_eq "$(test -f "$EX/europe_dach-latest.osm.pbf.incremental.partial.osm.pbf" && echo yes || echo no)" "yes" \
  "partial temp not deleted by budget"
assert_contains "$(cat "$TMP/out_budget.txt")" "over_budget_gib=1" "logs over_budget drop"

# If dach was dropped, its poly companion should be gone; if kept, poly stays.
if [[ ! -f "$EX/europe_dach-latest.osm.pbf" ]]; then
  assert_eq "$(test -f "$EX/europe_dach.poly" && echo yes || echo no)" "no" \
    "poly companion removed when dach PBF dropped"
else
  assert_eq "$(test -f "$EX/europe_dach.poly" && echo yes || echo no)" "yes" \
    "poly companion kept when dach PBF kept"
fi

echo "=== common.sh defaults document VPS-sized knobs ==="
# shellcheck disable=SC2034
source /dev/null
# Grep shipped defaults without sourcing load_config (avoids mkdir under real tree).
assert_contains "$(grep -E 'NAVI_HELD_PBF_BUDGET_GIB:=' "${SCRIPT_DIR}/lib/common.sh")" \
  "40" "default budget 40 GiB"
assert_contains "$(grep -E 'NAVI_HELD_PBF_MAX_MIB:=' "${SCRIPT_DIR}/lib/common.sh")" \
  "512" "default max 512 MiB"

if [[ "$FAIL" -ne 0 ]]; then
  echo "FAILED ${FAIL} checks (${PASS} passed)" >&2
  cat "$TMP"/out_*.txt >&2 || true
  exit 1
fi
echo "ALL PASS (${PASS})"
