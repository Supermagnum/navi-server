#!/usr/bin/env bash
# Step-5 wiring: run-weekly.sh passes --prefer-incremental (unless --force-fetch).
# Also asserts exit-10 → full-fetch fallback remains in fetch-extracts.sh.
# Isolated — does not run a live weekly bake or touch published packs.
#
# Usage: ./test-weekly-incremental-wiring.sh

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

assert_not_contains() {
  local hay="$1" needle="$2" name="$3"
  if grep -Fq -- "$needle" <<<"$hay"; then
    echo "FAIL $name unexpectedly_has=${needle@Q}"
    FAIL=$((FAIL + 1))
  else
    echo "PASS $name"
    PASS=$((PASS + 1))
  fi
}

echo "=== run-weekly.sh fetch wiring ==="
# Extract the fetch_args construction block (between "# 1. Fetch" and convert).
fetch_block="$(
  awk '
    /^# 1\. Fetch/ {grab=1}
    grab {print}
    /^# 2\. Convert/ {exit}
  ' "${SCRIPT_DIR}/run-weekly.sh"
)"
assert_contains "$fetch_block" "--prefer-incremental" \
  "weekly fetch block mentions --prefer-incremental"
assert_contains "$fetch_block" "FORCE_FETCH" \
  "weekly fetch block branches on FORCE_FETCH"
# Prefer-incremental must be the non-force path (else branch), not bundled with --force.
assert_contains "$fetch_block" "fetch_args+=(--prefer-incremental)" \
  "weekly adds --prefer-incremental via fetch_args"
assert_contains "$fetch_block" "fetch_args+=(--force)" \
  "weekly still supports --force-fetch via fetch_args+=(--force)"

echo "=== --force-fetch must not also pass --prefer-incremental ==="
# Simulate the same branching logic as run-weekly (unit, no network).
simulate() {
  local FORCE_FETCH="$1"
  local fetch_args=()
  if [[ "$FORCE_FETCH" -eq 1 ]]; then
    fetch_args+=(--force)
  else
    fetch_args+=(--prefer-incremental)
  fi
  printf '%s\n' "${fetch_args[*]}"
}
assert_eq "$(simulate 0)" "--prefer-incremental" "FORCE_FETCH=0 => --prefer-incremental only"
assert_eq "$(simulate 1)" "--force" "FORCE_FETCH=1 => --force only"
assert_not_contains "$(simulate 1)" "prefer-incremental" "FORCE_FETCH=1 has no prefer-incremental"

echo "=== fetch-extracts.sh exit-10 fallback still present ==="
fallback="$(
  awk '
    /inc_rc/ {grab=1}
    grab {print}
    /falling back to full fetch/ {print; exit}
  ' "${SCRIPT_DIR}/fetch-extracts.sh"
)"
assert_contains "$fallback" 'inc_rc' "fetch-extracts captures incremental rc"
assert_contains "$fallback" '-ne 10' "non-10 incremental rc is hard failure"
assert_contains "$fallback" 'falling back to full fetch' \
  "exit 10 logs fallback to full fetch"

if [[ "$FAIL" -ne 0 ]]; then
  echo "FAILED ${FAIL} checks (${PASS} passed)" >&2
  exit 1
fi
echo "ALL PASS (${PASS})"
