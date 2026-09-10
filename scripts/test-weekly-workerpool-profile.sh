#!/usr/bin/env bash
# Step-6: weekly path relies on WorkerPoolPlan only — no second concurrency
# mechanism and no weekly override of NAVI_TILE_BUILD_CONCURRENCY.
#
# Usage: ./test-weekly-workerpool-profile.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

assert_not_match() {
  local file="$1" pattern="$2" name="$3"
  if rg -n -- "$pattern" "$file" >/dev/null 2>&1; then
    echo "FAIL $name matched=${pattern@Q}"
    rg -n -- "$pattern" "$file" || true
    FAIL=$((FAIL + 1))
  else
    echo "PASS $name"
    PASS=$((PASS + 1))
  fi
}

WEEKLY="${SCRIPT_DIR}/run-weekly.sh"

echo "=== weekly does not set NAVI_TILE_BUILD_CONCURRENCY ==="
# Ban assignments / exports that pin convert concurrency. Mentions in log
# strings (…CONCURRENCY=${NAVI_TILE_BUILD_CONCURRENCY}…) are allowed.
if rg -n -e '^[[:space:]]*export[[:space:]]+NAVI_TILE_BUILD_CONCURRENCY=' \
      -e '^[[:space:]]*NAVI_TILE_BUILD_CONCURRENCY=[0-9]' \
      "$WEEKLY" >/dev/null 2>&1; then
  echo "FAIL run-weekly.sh assigns/exports NAVI_TILE_BUILD_CONCURRENCY"
  rg -n -e 'NAVI_TILE_BUILD_CONCURRENCY=' "$WEEKLY" || true
  FAIL=$((FAIL + 1))
else
  echo "PASS run-weekly.sh does not assign/export NAVI_TILE_BUILD_CONCURRENCY"
  PASS=$((PASS + 1))
fi
assert_contains "$(cat "$WEEKLY")" "WorkerPoolPlan" \
  "run-weekly.sh documents WorkerPoolPlan"
assert_contains "$(cat "$WEEKLY")" "weekly does not set NAVI_TILE_BUILD_CONCURRENCY" \
  "run-weekly.sh logs that it does not set the override"

echo "=== no second parallel convert / smoke path from weekly ==="
assert_not_match "$WEEKLY" 'run-planet-smoke-parallel' \
  "weekly does not call run-planet-smoke-parallel"
assert_not_match "$WEEKLY" 'run-planet-leaves' \
  "weekly does not call planet-leaves orchestrators"
assert_not_match "$WEEKLY" 'ThreadPoolExecutor|xargs -P|GNU parallel' \
  "weekly has no shell parallel job launcher"
# Convert is sequential: either convert-region.sh --all or a for-loop per region.
assert_contains "$(cat "$WEEKLY")" 'convert-region.sh' \
  "weekly uses convert-region.sh"

echo "=== no hardcoded 32-thread / 96 GiB assumptions in weekly ==="
assert_not_match "$WEEKLY" '\b96\b' "weekly has no literal 96"
# Allow only non-concurrency uses of 32 if any; ban common big-box pins.
assert_not_match "$WEEKLY" 'threads.*=.*32|32.*thread|nproc.*32' \
  "weekly has no 32-thread pin"

echo "=== convert binary still owns WorkerPoolPlan ==="
assert_contains "$(cat "${SCRIPT_DIR}/../navi-indexed-convert/src/main.rs")" \
  "WorkerPoolPlan::detect" \
  "navi-indexed-convert calls WorkerPoolPlan::detect"
assert_contains "$(cat "${SCRIPT_DIR}/../pack-convert-core/src/routing/workers.rs")" \
  "usable_ram_gib" \
  "WorkerPoolPlan reads MemAvailable via usable_ram_gib"

echo "=== WARN path when env already pins concurrency ==="
# Source the log branch logic without running a full bake: ensure the WARN
# string exists next to the env check.
warn_block="$(
  awk '/NAVI_TILE_BUILD_CONCURRENCY:-/,/unset _host/' "$WEEKLY"
)"
assert_contains "$warn_block" "log_warn" \
  "weekly WARNs when NAVI_TILE_BUILD_CONCURRENCY is pre-set"
assert_contains "$warn_block" "already set in the environment" \
  "WARN text mentions environment pin"

if [[ "$FAIL" -ne 0 ]]; then
  echo "FAILED ${FAIL} checks (${PASS} passed)" >&2
  exit 1
fi
echo "ALL PASS (${PASS})"
