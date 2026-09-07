#!/usr/bin/env bash
# Unit / integration tests for transient fetch retry (mock HTTP server).
# Does not touch the live planet-leaves bake.
#
# Usage: ./test-fetch-transient-retry.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/fetch_http_classify.sh
source "${SCRIPT_DIR}/lib/fetch_http_classify.sh"

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

# --- classify unit tests ---
hdr="$(mktemp)"
printf 'HTTP/1.1 502 Bad Gateway\r\n\r\n' >"$hdr"
classify_fetch_failure 22 "$hdr"
assert_eq "$FETCH_FAIL_CLASS" "transient" "502 -> transient"

printf 'HTTP/1.1 503 Service Unavailable\r\n\r\n' >"$hdr"
classify_fetch_failure 22 "$hdr"
assert_eq "$FETCH_FAIL_CLASS" "transient" "503 -> transient"

printf 'HTTP/1.1 404 Not Found\r\n\r\n' >"$hdr"
classify_fetch_failure 22 "$hdr"
assert_eq "$FETCH_FAIL_CLASS" "needs_human" "404 -> needs_human"

printf 'HTTP/1.1 401 Unauthorized\r\n\r\n' >"$hdr"
classify_fetch_failure 22 "$hdr"
assert_eq "$FETCH_FAIL_CLASS" "needs_human" "401 -> needs_human"

printf 'HTTP/1.1 500 Internal Server Error\r\n\r\n' >"$hdr"
classify_fetch_failure 22 "$hdr"
assert_eq "$FETCH_FAIL_CLASS" "ambiguous" "500 -> ambiguous (fail-safe)"

classify_fetch_failure 28 ""
assert_eq "$FETCH_FAIL_CLASS" "transient" "curl timeout 28 -> transient"

classify_fetch_failure 6 ""
assert_eq "$FETCH_FAIL_CLASS" "transient" "DNS 6 -> transient"

classify_fetch_failure 35 ""
assert_eq "$FETCH_FAIL_CLASS" "ambiguous" "SSL 35 -> ambiguous"
rm -f "$hdr"

# --- production defaults (integration cases below override with short budgets) ---
# Tuned 2026-09-07: 7200s / 60s probe (was 3600s / 30s).
default_budget="$(
  awk -F= '/^: "\$\{NAVI_FETCH_TRANSIENT_BUDGET_SECS:=/{
    gsub(/[^0-9]/, "", $2); print $2; exit
  }' "${SCRIPT_DIR}/fetch-extracts.sh"
)"
default_interval="$(
  awk -F= '/^: "\$\{NAVI_FETCH_RECOVERY_INTERVAL_SECS:=/{
    gsub(/[^0-9]/, "", $2); print $2; exit
  }' "${SCRIPT_DIR}/fetch-extracts.sh"
)"
default_interval_lib="$(
  awk -F= '/NAVI_FETCH_RECOVERY_INTERVAL_SECS:-[0-9]+/{
    if (match($0, /:-[0-9]+/)) print substr($0, RSTART+2, RLENGTH-2); exit
  }' "${SCRIPT_DIR}/lib/fetch_http_classify.sh"
)"
assert_eq "$default_budget" "7200" "default NAVI_FETCH_TRANSIENT_BUDGET_SECS=7200"
assert_eq "$default_interval" "60" "default NAVI_FETCH_RECOVERY_INTERVAL_SECS=60"
assert_eq "$default_interval_lib" "60" "fetch_http_classify wait_url_recovered default interval=60"

# --- mock server integration ---
TMP="$(mktemp -d)"
cleanup() {
  if [[ -n "${MOCK_PID:-}" ]] && kill -0 "$MOCK_PID" 2>/dev/null; then
    kill "$MOCK_PID" 2>/dev/null || true
    wait "$MOCK_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

python3 - "$TMP" <<'PY' &
import sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

state_dir = Path(sys.argv[1])
port_file = state_dir / "port"
hits_502 = {"n": 0}
hits_404 = {"n": 0}
lock = threading.Lock()

class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_HEAD(self):
        self.do_GET(head=True)

    def do_GET(self, head=False):
        path = self.path.split("?", 1)[0]
        if path == "/flaky.pbf":
            with lock:
                hits_502["n"] += 1
                n = hits_502["n"]
            if n <= 2:
                body = b"Bad Gateway"
                self.send_response(502)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head:
                    self.wfile.write(body)
                return
            body = b"OSM_PBF_FAKE_OK_PAYLOAD_xxxxxxxx"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if path == "/missing.pbf":
            with lock:
                hits_404["n"] += 1
            body = b"not found"
            self.send_response(404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if path == "/always502.pbf":
            body = b"Bad Gateway"
            self.send_response(502)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

httpd = HTTPServer(("127.0.0.1", 0), H)
port_file.write_text(str(httpd.server_address[1]))
httpd.serve_forever()
PY
MOCK_PID=$!

for _ in $(seq 1 50); do
  [[ -f "$TMP/port" ]] && break
  sleep 0.05
done
PORT="$(cat "$TMP/port")"
BASE="http://127.0.0.1:${PORT}"

# Isolated pack root so fetch-extracts does not touch live data.
PACK="$TMP/pack"
mkdir -p "$PACK/scratch/extracts" "$PACK/state/regions" "$PACK/logs"
cat >"$PACK/config.env" <<EOF
NAVI_PACK_ROOT=$PACK
NAVI_REGIONS_CONF=$PACK/regions.conf
NAVI_HTTP_TIMEOUT_SECS=10
NAVI_FETCH_TRANSIENT_BUDGET_SECS=120
NAVI_FETCH_BACKOFF_INITIAL_SECS=1
NAVI_FETCH_BACKOFF_MAX_SECS=2
NAVI_FETCH_RECOVERY_CLEAN_NEED=1
NAVI_FETCH_RECOVERY_INTERVAL_SECS=1
EOF
printf 'test_flaky\turl:%s/flaky.pbf\n' "$BASE" >"$PACK/regions.conf"
printf 'test_missing\turl:%s/missing.pbf\n' "$BASE" >>"$PACK/regions.conf"
printf 'test_always502\turl:%s/always502.pbf\n' "$BASE" >>"$PACK/regions.conf"

export NAVI_PACK_CONFIG="$PACK/config.env"
export NAVI_PACK_ROOT="$PACK"
export NAVI_REGIONS_CONF="$PACK/regions.conf"
# Ensure parent-shell live paths do not override via load_config preserve logic.
unset NAVI_EXTRACTS_DIR NAVI_SCRATCH_DIR NAVI_STATE_DIR NAVI_LOG_DIR || true

echo "=== integration: flaky 502 then 200 recovers ==="
set +e
env NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" NAVI_REGIONS_CONF="$PACK/regions.conf" \
  "${SCRIPT_DIR}/fetch-extracts.sh" --force test_flaky >"$TMP/out_flaky.txt" 2>&1
rc=$?
set -e
if [[ $rc -eq 0 && -s "$PACK/scratch/extracts/test_flaky-latest.osm.pbf" ]]; then
  echo "PASS flaky recovers without dying"
  PASS=$((PASS + 1))
else
  echo "FAIL flaky recover rc=$rc"
  cat "$TMP/out_flaky.txt"
  FAIL=$((FAIL + 1))
fi
rg -q 'class=transient' "$TMP/out_flaky.txt" && { echo "PASS flaky logged transient"; PASS=$((PASS+1)); } || { echo "FAIL no transient log"; FAIL=$((FAIL+1)); }

echo "=== integration: 404 pauses immediately (no long retry) ==="
set +e
env NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" NAVI_REGIONS_CONF="$PACK/regions.conf" \
  "${SCRIPT_DIR}/fetch-extracts.sh" --force test_missing >"$TMP/out_404.txt" 2>&1
rc=$?
set -e
if [[ $rc -ne 0 ]] && rg -q 'class=needs_human' "$TMP/out_404.txt"; then
  echo "PASS 404 needs_human immediate fail"
  PASS=$((PASS + 1))
else
  echo "FAIL 404 handling rc=$rc"
  cat "$TMP/out_404.txt"
  FAIL=$((FAIL + 1))
fi
# Should not have burned the full budget sleeping
if ! rg -q 'budget exhausted' "$TMP/out_404.txt"; then
  echo "PASS 404 did not exhaust transient budget"
  PASS=$((PASS + 1))
else
  echo "FAIL 404 exhausted budget unexpectedly"
  FAIL=$((FAIL + 1))
fi

echo "=== integration: always-502 exhausts budget then fails ==="
# Shrink budget for this case
sed -i 's/NAVI_FETCH_TRANSIENT_BUDGET_SECS=120/NAVI_FETCH_TRANSIENT_BUDGET_SECS=8/' "$PACK/config.env"
set +e
env NAVI_PACK_CONFIG="$PACK/config.env" NAVI_PACK_ROOT="$PACK" NAVI_REGIONS_CONF="$PACK/regions.conf" \
  "${SCRIPT_DIR}/fetch-extracts.sh" --force test_always502 >"$TMP/out_502.txt" 2>&1
rc=$?
set -e
if [[ $rc -ne 0 ]] && rg -q 'budget exhausted|recovery failed' "$TMP/out_502.txt"; then
  echo "PASS always-502 exhausts budget"
  PASS=$((PASS + 1))
else
  echo "FAIL always-502 rc=$rc"
  cat "$TMP/out_502.txt"
  FAIL=$((FAIL + 1))
fi
# Short-budget config sets RECOVERY_INTERVAL_SECS=1 — confirm cadence is applied.
if rg -q 'fetch recovery sleep 1s' "$TMP/out_502.txt"; then
  echo "PASS always-502 used configured 1s recovery interval"
  PASS=$((PASS + 1))
else
  echo "FAIL always-502 did not log 1s recovery sleep (interval not applied?)"
  cat "$TMP/out_502.txt"
  FAIL=$((FAIL + 1))
fi

echo "=== summary pass=$PASS fail=$FAIL ==="
[[ "$FAIL" -eq 0 ]]
