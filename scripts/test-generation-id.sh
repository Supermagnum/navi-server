#!/usr/bin/env bash
# Concurrent collision test for generation_id().
# Usage: ./test-generation-id.sh [workers] [ids_per_worker]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

WORKERS="${1:-16}"
PER="${2:-64}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "spawning workers=${WORKERS} ids_per_worker=${PER}"
for w in $(seq 1 "$WORKERS"); do
  (
    for i in $(seq 1 "$PER"); do
      generation_id "w${w}"
    done
  ) >"${TMP}/w${w}.txt" &
done
wait

TOTAL=$((WORKERS * PER))
UNIQUE="$(cat "${TMP}"/w*.txt | sort -u | wc -l)"
LINES="$(cat "${TMP}"/w*.txt | wc -l)"

echo "lines=${LINES} unique=${UNIQUE} expected=${TOTAL}"
if [[ "$LINES" -ne "$TOTAL" ]]; then
  echo "FAIL: line count mismatch" >&2
  exit 1
fi
if [[ "$UNIQUE" -ne "$TOTAL" ]]; then
  echo "FAIL: collisions detected ($((TOTAL - UNIQUE)) duplicates)" >&2
  sort "${TMP}"/w*.txt | uniq -d | head
  exit 1
fi
echo "PASS: no generation_id collisions"
