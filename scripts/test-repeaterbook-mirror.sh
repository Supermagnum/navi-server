#!/usr/bin/env bash
# Dry-run RepeaterBook mirror against synthetic fixtures (no network).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FIX="${ROOT}/plugins/repeaterbook/tests/fixtures"
TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

command -v zstd >/dev/null 2>&1 || { echo "FAILED: zstd required for dry-run"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "FAILED: python3 required"; exit 1; }

export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
PUB="${TMP}/published/repeaters"

python3 -m plugins.repeaterbook \
  --dry-run-fixtures "$FIX" \
  --publish-root "$PUB"

test -f "${PUB}/current.json"
test -f "${PUB}/no/"*"/manifest.json"
test -f "${PUB}/no/"*"/repeaters.json.zst"

echo "=== current.json ==="
python3 -m json.tool "${PUB}/current.json"

echo "=== manifest.json ==="
MAN="$(ls -1 "${PUB}/no/"*/manifest.json | head -1)"
python3 -m json.tool "$MAN"

# Grep guard: no app_ / rbuapp_ token-looking strings in published tree
if grep -RInE '\b(app_|rbuapp_)[A-Za-z0-9._-]{8,}\b' "$PUB" >/dev/null 2>&1; then
  echo "FAILED: token-like string found under publish root"
  exit 1
fi

echo "OK repeaterbook dry-run fixtures -> ${PUB}"
