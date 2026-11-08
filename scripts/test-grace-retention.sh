#!/usr/bin/env bash
# Grace / count published-pack retention: fixture scrub via cleanup.sh --dry-run
# and pack_retention unit tests. Isolated mktemp tree — never touches live data.
#
# Usage: ./test-grace-retention.sh

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

assert_dir() {
  local path="$1" name="$2"
  if [[ -d "$path" ]]; then
    echo "PASS $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL $name missing_dir=${path@Q}"
    FAIL=$((FAIL + 1))
  fi
}

assert_no_dir() {
  local path="$1" name="$2"
  if [[ ! -d "$path" ]]; then
    echo "PASS $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL $name still_present=${path@Q}"
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

echo "=== python unit tests (pack_retention) ==="
(
  cd "${SCRIPT_DIR}/lib"
  python3 -m unittest test_pack_retention -v
)
echo "PASS python unit tests"
PASS=$((PASS + 1))

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

write_gen() {
  local dir="$1" gen="$2" payload="${3:-blob}"
  mkdir -p "$dir/$gen"
  printf '%s' "$payload" >"$dir/$gen/blob.bin"
  printf '{"schema":1,"generation":"%s","files":{"blob.bin":{"sha256":"%s","bytes":%s}}}\n' \
    "$gen" "$(printf '%064d' 0)" "${#payload}" >"$dir/$gen/manifest.json"
  printf '%064d  blob.bin\n' 0 >"$dir/$gen/checksums.sha256"
}

REGION="$PACK/published/packs/europe/andorra"
write_gen "$REGION" "20260901T000000Z-1-old" "old-payload"
write_gen "$REGION" "20260920T000000Z-2-new" "new-payload"
python3 - "$PACK/published" <<'PY'
import json, sys
from pathlib import Path
pub = Path(sys.argv[1])
cur = {
  "schema": 1,
  "generation": "test",
  "created_unix": 0,
  "packs_base": "/packs",
  "layout": "geofabrik-path",
  "regions": [{
    "region_id": "europe/andorra",
    "generation": "20260920T000000Z-2-new",
    "manifest_url": "/packs/europe/andorra/20260920T000000Z-2-new/manifest.json",
  }],
}
(pub / "current.json").write_text(json.dumps(cur, indent=2) + "\n", encoding="utf-8")
PY

cat >"$PACK/config.env" <<EOF
NAVI_PACK_ROOT=$PACK
NAVI_REGIONS_CONF=$PACK/regions.conf
NAVI_KEEP_GENERATIONS=2
NAVI_RETENTION_MODE=grace
NAVI_GRACE_DAYS=7
NAVI_BAKE_DELTA_H=0
EOF
printf 'europe_andorra\tgeofabrik:europe/andorra\n' >"$PACK/regions.conf"

echo "=== cleanup --dry-run exits 0 and leaves tree intact ==="
set +e
NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" \
  "${SCRIPT_DIR}/cleanup.sh" --skip-quota-gate --dry-run >"$TMP/cleanup-dry.txt" 2>&1
rc=$?
set -e
assert_eq "$rc" "0" "cleanup --dry-run exit 0"
assert_dir "$REGION/20260901T000000Z-1-old" "dry-run keeps old gen on disk"
assert_dir "$REGION/20260920T000000Z-2-new" "dry-run keeps current gen on disk"
assert_contains "$(cat "$TMP/cleanup-dry.txt")" "pack-retention" "dry-run logs pack-retention"

NOW_UNIX="$(
  cd "${SCRIPT_DIR}/lib"
  python3 - <<'PY'
from pack_retention import parse_gen_publish_unix
print(parse_gen_publish_unix("20260920T000000Z-2-new") + 30 * 86400)
PY
)"

echo "=== apply grace retention outside window deletes prior only ==="
(
  cd "${SCRIPT_DIR}/lib"
  python3 pack_retention.py \
    --published "$PACK/published" --mode grace --keep 2 --grace-days 7 \
    --now-unix "$NOW_UNIX" --apply
) >"$TMP/apply.txt" 2>&1
assert_no_dir "$REGION/20260901T000000Z-1-old" "old gen deleted after grace"
assert_dir "$REGION/20260920T000000Z-2-new" "current gen retained after grace apply"

echo "=== missing current.json: dry-run aborts pack deletes ==="
rm -f "$PACK/published/current.json"
write_gen "$REGION" "20260901T000000Z-1-old" "old-again"
set +e
NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" \
  "${SCRIPT_DIR}/cleanup.sh" --skip-quota-gate --dry-run >"$TMP/no-cur.txt" 2>&1
rc=$?
set -e
assert_eq "$rc" "0" "missing current.json dry-run still exits 0"
assert_dir "$REGION/20260901T000000Z-1-old" "no delete without current.json"
assert_contains "$(cat "$TMP/no-cur.txt")" "pack-retention ABORT" \
  "logs ABORT when current.json missing"

if [[ "$FAIL" -ne 0 ]]; then
  echo "FAILED ${FAIL} checks (${PASS} passed)" >&2
  cat "$TMP"/*.txt >&2 || true
  exit 1
fi
echo "ALL PASS (${PASS})"
