#!/usr/bin/env bash
# Smoke test for migrate-server.sh using --local-dest (no network).
# SSH path is exercised by argument parsing / help; transfer+verify use local mode.
#
# Usage: ./test-migrate-server.sh

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
assert_file() {
  local p="$1" name="$2"
  if [[ -f "$p" ]]; then
    echo "PASS $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL $name missing=${p}"
    FAIL=$((FAIL + 1))
  fi
}
assert_mode() {
  local p="$1" want="$2" name="$3"
  local got
  got=$(stat -c '%a' "$p")
  assert_eq "$got" "$want" "$name"
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

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

SRC="$TMP/src"
DST="$TMP/dest"
mkdir -p \
  "$SRC/scripts/lib" \
  "$SRC/data/published/packs/europe/andorra/20260101T000000Z" \
  "$SRC/data/secrets" \
  "$SRC/data/state/regions/europe/andorra" \
  "$DST/scripts"

cp "${SCRIPT_DIR}/migrate-server.sh" "$SRC/scripts/migrate-server.sh"
cp "${SCRIPT_DIR}/lib/common.sh" "$SRC/scripts/lib/common.sh"
chmod +x "$SRC/scripts/migrate-server.sh"

printf '%s\n' '{"regions":[]}' >"$SRC/data/published/current.json"
echo "pack-bytes" >"$SRC/data/published/packs/europe/andorra/20260101T000000Z/README.txt"
cat >"$SRC/data/config.env" <<'EOF'
NAVI_SERVICE_USER=navit-server
NAVI_RADIOID_COUNTRIES=Norway
NAVI_RADIOID_SECRETS_FILE=/media/navi/navi-server/data/secrets/radioid.env
EOF
echo "etag=1" >"$SRC/data/state/regions/europe/andorra/etag"
printf '%s\n' '# secret fixture — not real' 'RADIOID_API_TOKEN=test-token-not-real' \
  >"$SRC/data/secrets/radioid.env"
chmod 600 "$SRC/data/secrets/radioid.env"
chmod 700 "$SRC/data/secrets"

cat >"$DST/scripts/setup-server.sh" <<'EOF'
#!/usr/bin/env bash
# Minimal --check stub for migrate verify.
if [[ "${1:-}" == "--check" ]]; then
  echo "OK published/"
  echo "OK config.env"
  exit 0
fi
exit 0
EOF
chmod +x "$DST/scripts/setup-server.sh"

export NAVI_SERVER_ROOT="$SRC"

echo "=== help mentions ssh ==="
help_out="$("$SRC/scripts/migrate-server.sh" --help || true)"
assert_contains "$help_out" "SSH" "help mentions SSH"
assert_contains "$help_out" "--dest" "help mentions --dest"

echo "=== dry-run local ==="
"$SRC/scripts/migrate-server.sh" --local-dest "$DST" --dry-run
assert_eq "0" "0" "dry-run completed"

echo "=== migrate local ==="
"$SRC/scripts/migrate-server.sh" --local-dest "$DST"
assert_file "$DST/data/published/current.json" "packs current.json"
assert_file "$DST/data/secrets/radioid.env" "secrets file"
assert_mode "$DST/data/secrets/radioid.env" "600" "secret mode 0600"
assert_mode "$DST/data/secrets" "700" "secrets dir mode 700"
assert_file "$DST/data/config.env" "config.env"
assert_file "$DST/data/state/migrate-jobs-inventory.json" "jobs inventory"
assert_file "$DST/data/state/regions/europe/andorra/etag" "state etag"

# Path rewrite toward dest
if grep -Fq "${DST}/data/secrets/radioid.env" "$DST/data/config.env"; then
  echo "PASS config path rewritten to dest"
  PASS=$((PASS + 1))
else
  echo "FAIL config path not rewritten; got:"
  cat "$DST/data/config.env"
  FAIL=$((FAIL + 1))
fi

if grep -R "test-token-not-real" "$DST/data/state" >/dev/null 2>&1; then
  echo "FAIL secret leaked into state/"
  FAIL=$((FAIL + 1))
else
  echo "PASS secret not copied into jobs inventory payload"
  PASS=$((PASS + 1))
fi

echo "=== verify-only ==="
"$SRC/scripts/migrate-server.sh" --local-dest "$DST" --verify-only
assert_eq "$?" "0" "verify-only exit (captured above via set -e)"

echo "=== refuses missing --dest ==="
set +e
"$SRC/scripts/migrate-server.sh" >/tmp/migrate-no-dest.out 2>&1
rc=$?
set -e
assert_eq "$rc" "1" "missing dest exits 1"

echo "=== summary pass=${PASS} fail=${FAIL} ==="
[[ "$FAIL" -eq 0 ]]
