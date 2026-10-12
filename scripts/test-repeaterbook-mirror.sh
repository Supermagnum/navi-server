#!/usr/bin/env bash
# Dry-run RepeaterBook only (fixtures). Prefer ./test-repeater-mirror.sh for both.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FIX="${ROOT}/plugins/repeaterbook/tests/fixtures"
TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT
command -v zstd >/dev/null 2>&1 || { echo "FAILED: zstd required"; exit 1; }
export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
PUB="${TMP}/published/repeaters"
python3 -m plugins.repeaterbook --dry-run-fixtures "$FIX" --publish-root "$PUB"
test -f "${PUB}/current.json"
test -f "${PUB}/repeaterbook/no/"*/manifest.json
python3 -m json.tool "${PUB}/current.json" | head -40
echo "OK repeaterbook dry-run -> ${PUB}"
