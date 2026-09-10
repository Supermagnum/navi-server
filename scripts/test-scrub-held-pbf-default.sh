#!/usr/bin/env bash
# Scrub defaults must retain held PBFs (replication headers) unless opt-in prune.
# Mirrors systemd/navi-pack-scrub.service (--skip-quota-gate, no prune flag).
# Isolated mktemp pack root — does not touch the live published tree.
#
# Usage: ./test-scrub-held-pbf-default.sh

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

echo "=== navi-pack-scrub.service ExecStart keeps --no-extracts ==="
unit="${SCRIPT_DIR}/../systemd/navi-pack-scrub.service"
exec_line="$(grep -E '^ExecStart=' "$unit")"
assert_contains "$exec_line" "--no-extracts" "scrub unit ExecStart has --no-extracts"
assert_contains "$exec_line" "--skip-quota-gate" "scrub unit ExecStart has --skip-quota-gate"
assert_contains "$exec_line" "cleanup.sh" "scrub unit invokes cleanup.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PACK="$TMP/pack"
mkdir -p "$PACK/scratch/extracts" "$PACK/scratch/convert" "$PACK/state" \
  "$PACK/logs" "$PACK/staging" "$PACK/generations" "$PACK/published/packs" \
  "$PACK/live_gen"
ln -sfn "$PACK/live_gen" "$PACK/live"
ln -sfn "$PACK/live_gen" "$PACK/previous"
mkdir -p "$PACK/generations/20260901T000000Z" "$PACK/generations/20260902T000000Z"
touch "$PACK/generations/20260901T000000Z/.keep" "$PACK/generations/20260902T000000Z/.keep"

# Aged held PBF with osmosis-style replication headers (osmium not required —
# cleanup only age-deletes by mtime; headers document incremental intent).
held="$PACK/scratch/extracts/europe_andorra-latest.osm.pbf"
printf 'held-pbf-with-replication-intent' >"$held"
touch -d '40 days ago' "$held"

cat >"$PACK/config.env" <<EOF
NAVI_PACK_ROOT=$PACK
NAVI_REGIONS_CONF=$PACK/regions.conf
NAVI_KEEP_GENERATIONS=2
NAVI_EXTRACT_KEEP_DAYS=21
NAVI_BAKE_DELTA_H=0
EOF
printf 'europe_andorra\tgeofabrik:europe/andorra\n' >"$PACK/regions.conf"

echo "=== scrub-style default (like unit): aged held PBF survives ==="
set +e
env -u NAVI_SCRUB_PRUNE_EXTRACTS \
  NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" \
  "${SCRIPT_DIR}/cleanup.sh" --skip-quota-gate >"$TMP/out_default.txt" 2>&1
rc_def=$?
set -e
assert_eq "$rc_def" "0" "scrub-style cleanup exit 0"
assert_eq "$(test -f "$held" && echo yes || echo no)" "yes" \
  "held PBF retained under scrub-style default"
assert_contains "$(cat "$TMP/out_default.txt")" "extract age-prune skipped" \
  "default scrub logs extract age-prune skipped"

echo "=== unit-equivalent flags: --skip-quota-gate --no-extracts ==="
printf 'held-pbf-with-replication-intent' >"$held"
touch -d '40 days ago' "$held"
set +e
env -u NAVI_SCRUB_PRUNE_EXTRACTS \
  NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" \
  "${SCRIPT_DIR}/cleanup.sh" --skip-quota-gate --no-extracts >"$TMP/out_unit.txt" 2>&1
rc_unit=$?
set -e
assert_eq "$rc_unit" "0" "unit-equivalent cleanup exit 0"
assert_eq "$(test -f "$held" && echo yes || echo no)" "yes" \
  "held PBF retained with explicit --no-extracts"

echo "=== opt-in --prune-extracts still deletes aged held PBF ==="
printf 'held-pbf-with-replication-intent' >"$held"
touch -d '40 days ago' "$held"
set +e
env -u NAVI_SCRUB_PRUNE_EXTRACTS \
  NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" \
  "${SCRIPT_DIR}/cleanup.sh" --skip-quota-gate --prune-extracts >"$TMP/out_prune.txt" 2>&1
rc_prune=$?
set -e
assert_eq "$rc_prune" "0" "--prune-extracts exit 0"
assert_eq "$(test -f "$held" && echo yes || echo no)" "no" \
  "held PBF pruned with --prune-extracts"

echo "=== opt-in NAVI_SCRUB_PRUNE_EXTRACTS=1 prunes; --no-extracts wins over env ==="
printf 'held-pbf-with-replication-intent' >"$held"
touch -d '40 days ago' "$held"
set +e
env NAVI_SCRUB_PRUNE_EXTRACTS=1 \
  NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" \
  "${SCRIPT_DIR}/cleanup.sh" --skip-quota-gate >"$TMP/out_env.txt" 2>&1
rc_env=$?
set -e
assert_eq "$rc_env" "0" "NAVI_SCRUB_PRUNE_EXTRACTS=1 exit 0"
assert_eq "$(test -f "$held" && echo yes || echo no)" "no" \
  "held PBF pruned when NAVI_SCRUB_PRUNE_EXTRACTS=1"

printf 'held-pbf-with-replication-intent' >"$held"
touch -d '40 days ago' "$held"
set +e
env NAVI_SCRUB_PRUNE_EXTRACTS=1 \
  NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" \
  "${SCRIPT_DIR}/cleanup.sh" --skip-quota-gate --no-extracts >"$TMP/out_env_override.txt" 2>&1
rc_ovr=$?
set -e
assert_eq "$rc_ovr" "0" "env + --no-extracts exit 0"
assert_eq "$(test -f "$held" && echo yes || echo no)" "yes" \
  "--no-extracts overrides NAVI_SCRUB_PRUNE_EXTRACTS=1"

if [[ "$FAIL" -ne 0 ]]; then
  echo "FAILED ${FAIL} checks (${PASS} passed)" >&2
  cat "$TMP"/out_*.txt >&2 || true
  exit 1
fi
echo "ALL PASS (${PASS})"
