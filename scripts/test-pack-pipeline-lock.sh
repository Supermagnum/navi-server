#!/usr/bin/env bash
# Pack pipeline lock: scrub skips while bake holds; bake waits for scrub;
# lock is released after SIGKILL (flock on FD, no stale pidfile).
# Isolated mktemp tree — never touches live data.
#
# Usage: ./test-pack-pipeline-lock.sh

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

TMP="$(mktemp -d "${TMPDIR:-/tmp}/navi-pipeline-lock.XXXXXX")"
cleanup_tmp() { rm -rf "$TMP"; }
trap cleanup_tmp EXIT

PACK="$TMP/data"
export NAVI_PACK_ROOT="$PACK"
export NAVI_PACK_CONFIG="$PACK/config.env"
mkdir -p \
  "$PACK/state" "$PACK/logs" "$PACK/staging" "$PACK/generations" \
  "$PACK/published/packs" "$PACK/scratch/extracts" "$PACK/scratch/convert"
cat >"$PACK/config.env" <<EOF
NAVI_PACK_ROOT=$PACK
NAVI_KEEP_GENERATIONS=2
NAVI_RETENTION_MODE=count
NAVI_HELD_PBF_BUDGET_GIB=0
NAVI_HELD_PBF_MAX_MIB=0
EOF
# Reload helpers against the fixture tree.
load_config

LOCK="$(pack_pipeline_lock_path)"
mkdir -p "$(dirname "$LOCK")"

echo "=== bake-holds-lock -> scrub skips ==="
# Background holder mimics bake (exclusive flock on the shared lock file).
(
  exec 9>"$LOCK"
  flock 9
  # Signal ready, then hold until killed.
  echo ready >"$TMP/holder1.ready"
  sleep 120
) &
HOLDER1=$!
for _ in $(seq 1 50); do
  [[ -f "$TMP/holder1.ready" ]] && break
  sleep 0.05
done
[[ -f "$TMP/holder1.ready" ]] || { echo "FAIL holder1 never ready"; exit 1; }

set +e
OUT1="$("${SCRIPT_DIR}/cleanup.sh" --skip-quota-gate --no-extracts 2>&1)"
RC1=$?
set -e
assert_eq "$RC1" "0" "scrub exits 0 when lock held"
assert_contains "$OUT1" "skipped: bake in progress" "scrub logs skip message"
kill "$HOLDER1" 2>/dev/null || true
wait "$HOLDER1" 2>/dev/null || true

echo "=== scrub-holds-lock -> bake waits then acquires ==="
(
  exec 9>"$LOCK"
  flock 9
  echo ready >"$TMP/holder2.ready"
  # Hold briefly so the waiter blocks, then release.
  sleep 2
) &
HOLDER2=$!
for _ in $(seq 1 50); do
  [[ -f "$TMP/holder2.ready" ]] && break
  sleep 0.05
done
[[ -f "$TMP/holder2.ready" ]] || { echo "FAIL holder2 never ready"; exit 1; }

# Fresh shell state for lock helpers (previous scrub released / never acquired).
unset NAVI_PACK_PIPELINE_LOCK_HELD
NAVI_PACK_PIPELINE_LOCK_ACQUIRED_HERE=0
export NAVI_PACK_PIPELINE_LOCK_WAIT_SECS=15

START_WAIT="$(date +%s)"
set +e
WAIT_LOG="$(
  pack_pipeline_lock_acquire_wait "bake-test" 2>&1
  echo "ACQUIRED_RC=$?"
)"
WAIT_RC=$?
set -e
END_WAIT="$(date +%s)"
ELAPSED=$((END_WAIT - START_WAIT))

assert_eq "$WAIT_RC" "0" "bake acquire_wait exits 0 after scrub releases"
assert_contains "$WAIT_LOG" "pack-pipeline.lock: waiting" "bake logs waiting"
assert_contains "$WAIT_LOG" "pack-pipeline.lock: acquired" "bake logs acquired"
# Should have blocked ~2s (not instant).
if [[ "$ELAPSED" -ge 1 ]]; then
  echo "PASS bake waited at least 1s (elapsed=${ELAPSED}s)"
  PASS=$((PASS + 1))
else
  echo "FAIL bake did not wait (elapsed=${ELAPSED}s)"
  FAIL=$((FAIL + 1))
fi
pack_pipeline_lock_release || true
wait "$HOLDER2" 2>/dev/null || true

echo "=== lock released after SIGKILL ==="
(
  exec 9>"$LOCK"
  flock 9
  echo ready >"$TMP/holder3.ready"
  sleep 120
) &
HOLDER3=$!
for _ in $(seq 1 50); do
  [[ -f "$TMP/holder3.ready" ]] && break
  sleep 0.05
done
[[ -f "$TMP/holder3.ready" ]] || { echo "FAIL holder3 never ready"; exit 1; }

# Confirm lock is held (non-blocking try fails).
unset NAVI_PACK_PIPELINE_LOCK_HELD
NAVI_PACK_PIPELINE_LOCK_ACQUIRED_HERE=0
set +e
TRY_LOG="$(pack_pipeline_lock_try "probe" 2>&1)"
TRY_RC=$?
set -e
assert_eq "$TRY_RC" "1" "try fails while SIGKILL target holds lock"
assert_contains "$TRY_LOG" "skipped: bake in progress" "try logs skip while held"

kill -9 "$HOLDER3" 2>/dev/null || true
wait "$HOLDER3" 2>/dev/null || true
# Give the kernel a moment to close the dead process FDs.
sleep 0.1

unset NAVI_PACK_PIPELINE_LOCK_HELD
NAVI_PACK_PIPELINE_LOCK_ACQUIRED_HERE=0
set +e
AFTER_LOG="$(pack_pipeline_lock_try "after-sigkill" 2>&1)"
AFTER_RC=$?
set -e
assert_eq "$AFTER_RC" "0" "try succeeds after SIGKILL releases flock"
assert_contains "$AFTER_LOG" "pack-pipeline.lock: acquired" "acquired after SIGKILL"
pack_pipeline_lock_release || true

echo "=== nested HELD skips re-acquire (no deadlock) ==="
unset NAVI_PACK_PIPELINE_LOCK_HELD
NAVI_PACK_PIPELINE_LOCK_ACQUIRED_HERE=0
pack_pipeline_lock_acquire_wait "outer"
# Simulate child scrub under bake.
export NAVI_PACK_PIPELINE_LOCK_HELD=1
# Child must not set ACQUIRED_HERE — release in child must no-op.
NAVI_PACK_PIPELINE_LOCK_ACQUIRED_HERE=0
set +e
NEST_LOG="$(pack_pipeline_lock_try "nested-scrub" 2>&1)"
NEST_RC=$?
set -e
assert_eq "$NEST_RC" "0" "nested try succeeds without re-flock"
assert_contains "$NEST_LOG" "already held (nested)" "nested try logs nested"
pack_pipeline_lock_release || true
# Restore outer ownership and release for real.
NAVI_PACK_PIPELINE_LOCK_ACQUIRED_HERE=1
export NAVI_PACK_PIPELINE_LOCK_HELD=1
pack_pipeline_lock_release || true

echo ""
echo "pack-pipeline-lock tests: PASS=${PASS} FAIL=${FAIL}"
[[ "$FAIL" -eq 0 ]]
