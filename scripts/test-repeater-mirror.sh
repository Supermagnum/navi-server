#!/usr/bin/env bash
# Fixture dry-run for RepeaterBook + RadioID into one publish root (no network).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
RB_FIX="${ROOT}/plugins/repeaterbook/tests/fixtures"
RID_FIX="${ROOT}/plugins/radioid/tests/fixtures"
TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

command -v zstd >/dev/null 2>&1 || { echo "FAILED: zstd required"; exit 1; }
export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
PUB="${TMP}/published/repeaters"

python3 -m plugins.repeaterbook --dry-run-fixtures "$RB_FIX" --publish-root "$PUB"
python3 -m plugins.radioid --dry-run-fixtures "$RID_FIX" --publish-root "$PUB"

test -f "${PUB}/current.json"
test -d "${PUB}/repeaterbook/no"
test -d "${PUB}/radioid/no"

echo "=== current.json ==="
python3 -m json.tool "${PUB}/current.json"

python3 - <<PY
import json
from pathlib import Path
cur=json.loads(Path("${PUB}/current.json").read_text())
sources={s["source"] for s in cur["sources"]}
assert sources=={"repeaterbook","radioid"}, sources
print("OK sources", sorted(sources))
PY

if grep -RInE '\b(app_|rbuapp_)[A-Za-z0-9._-]{8,}\b' "$PUB" >/dev/null 2>&1; then
  echo "FAILED: token-like string under publish root"
  exit 1
fi
echo "OK repeater-mirror dry-run both sources -> ${PUB}"
