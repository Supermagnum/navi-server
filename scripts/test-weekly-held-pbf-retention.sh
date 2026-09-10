#!/usr/bin/env bash
# Step-4 wiring: weekly cleanup retains held PBFs (--no-extracts).
# Isolated mktemp pack root — does not touch the live published tree.
#
# Usage: ./test-weekly-held-pbf-retention.sh

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

echo "=== run-weekly.sh invokes cleanup with --no-extracts ==="
# Require the cleanup invocation (not a comment-only mention) to pass the flag.
cleanup_line="$(
  awk '
    /^[[:space:]]*"\$\{SCRIPT_DIR\}\/cleanup\.sh"/ { print; exit }
    /^[[:space:]]*\$\{SCRIPT_DIR\}\/cleanup\.sh/ { print; exit }
  ' "${SCRIPT_DIR}/run-weekly.sh"
)"
assert_contains "$cleanup_line" "--no-extracts" "run-weekly cleanup line has --no-extracts"
assert_contains "$cleanup_line" "--skip-quota-gate" "run-weekly cleanup line has --skip-quota-gate"

echo "=== cleanup --no-extracts keeps aged held PBF ==="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PACK="$TMP/pack"
mkdir -p "$PACK/scratch/extracts" "$PACK/scratch/convert" "$PACK/state" \
  "$PACK/logs" "$PACK/staging" "$PACK/generations" "$PACK/published/packs" \
  "$PACK/live_gen"
# Minimal live+previous so cleanup keep>=2 logic has protected paths.
ln -sfn "$PACK/live_gen" "$PACK/live"
ln -sfn "$PACK/live_gen" "$PACK/previous"
# Two generation dirs so keep=2 does not error on empty.
mkdir -p "$PACK/generations/20260901T000000Z" "$PACK/generations/20260902T000000Z"
touch "$PACK/generations/20260901T000000Z/.keep" "$PACK/generations/20260902T000000Z/.keep"

held="$PACK/scratch/extracts/europe_andorra-latest.osm.pbf"
printf 'held-pbf-payload' >"$held"
# Age beyond NAVI_EXTRACT_KEEP_DAYS so prune-extracts would delete it.
touch -d '40 days ago' "$held"

cat >"$PACK/config.env" <<EOF
NAVI_PACK_ROOT=$PACK
NAVI_REGIONS_CONF=$PACK/regions.conf
NAVI_KEEP_GENERATIONS=2
NAVI_EXTRACT_KEEP_DAYS=21
NAVI_BAKE_DELTA_H=0
EOF
printf 'europe_andorra\tgeofabrik:europe/andorra\n' >"$PACK/regions.conf"

set +e
env NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" \
  "${SCRIPT_DIR}/cleanup.sh" --skip-quota-gate --no-extracts >"$TMP/out_keep.txt" 2>&1
rc_keep=$?
set -e
assert_eq "$rc_keep" "0" "cleanup --no-extracts exit 0"
assert_eq "$(test -f "$held" && echo yes || echo no)" "yes" \
  "held PBF retained with --no-extracts"

# Control: same aged file is removed when extract prune is enabled.
printf 'held-pbf-payload' >"$held"
touch -d '40 days ago' "$held"
set +e
env NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" \
  "${SCRIPT_DIR}/cleanup.sh" --skip-quota-gate --prune-extracts >"$TMP/out_prune.txt" 2>&1
rc_prune=$?
set -e
assert_eq "$rc_prune" "0" "cleanup --prune-extracts exit 0"
assert_eq "$(test -f "$held" && echo yes || echo no)" "no" \
  "held PBF pruned without --no-extracts (control)"

if [[ "$FAIL" -ne 0 ]]; then
  echo "FAILED ${FAIL} checks (${PASS} passed)" >&2
  cat "$TMP/out_keep.txt" >&2 || true
  cat "$TMP/out_prune.txt" >&2 || true
  exit 1
fi
echo "ALL PASS (${PASS})"
