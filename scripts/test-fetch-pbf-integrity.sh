#!/usr/bin/env bash
# Regression tests for PBF integrity guards in fetch-extracts.sh:
#   - HTML soft-404 (HTTP 200 homepage body) must die before convert
#   - Published checksum URL failure must die and delete unverified PBF
#   - Happy path (binary PBF + matching .md5) still succeeds
#
# Does not touch the live planet-leaves bake.
#
# Usage: ./test-fetch-pbf-integrity.sh

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PASS=0
FAIL=0

TMP="$(mktemp -d)"
cleanup() {
  if [[ -n "${MOCK_PID:-}" ]] && kill -0 "$MOCK_PID" 2>/dev/null; then
    kill "$MOCK_PID" 2>/dev/null || true
    wait "$MOCK_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

# Non-HTML body (must not match the HTML soft-404 detector).
printf 'OSMHeader_fake_pbf_payload_for_integrity_test' >"$TMP/good.pbf"
GOOD_MD5="$(md5sum "$TMP/good.pbf" | awk '{print $1}')"
printf '%s  extract-latest.osm.pbf\n' "$GOOD_MD5" >"$TMP/good.md5"
cat >"$TMP/soft404.html" <<'EOF'
<!DOCTYPE html>
<html><head><title>Geofabrik Downloads</title></head><body>soft-404</body></html>
EOF

python3 - "$TMP" <<'PY' &
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

state_dir = Path(sys.argv[1])
port_file = state_dir / "port"
good_body = (state_dir / "good.pbf").read_bytes()
good_md5_line = (state_dir / "good.md5").read_bytes()
html_body = (state_dir / "soft404.html").read_bytes()

class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_HEAD(self):
        self.do_GET(head=True)

    def _send(self, code, body, content_type, head):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def do_GET(self, head=False):
        path = self.path.split("?", 1)[0]
        if path == "/ok/extract-latest.osm.pbf":
            self._send(200, good_body, "application/octet-stream", head)
            return
        if path == "/ok/extract-latest.osm.pbf.md5":
            self._send(200, good_md5_line, "text/plain", head)
            return
        # Soft-404: HTTP 200 HTML body (Enfield / Geofabrik homepage pattern)
        if path == "/html/extract-latest.osm.pbf":
            self._send(200, html_body, "text/html", head)
            return
        if path == "/html/extract-latest.osm.pbf.md5":
            self.send_response(404)
            self.end_headers()
            return
        # Valid-looking PBF but checksum URL 404 (must refuse unverified)
        if path == "/nocheck/extract-latest.osm.pbf":
            self._send(200, good_body, "application/octet-stream", head)
            return
        if path == "/nocheck/extract-latest.osm.pbf.md5":
            self.send_response(404)
            self.end_headers()
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

PACK="$TMP/pack"
mkdir -p "$PACK/scratch/extracts" "$PACK/state/regions" "$PACK/logs"
cat >"$PACK/config.env" <<EOF
NAVI_PACK_ROOT=$PACK
NAVI_REGIONS_CONF=$PACK/regions.conf
NAVI_GEOFABRIK_BASE=${BASE}
NAVI_HTTP_TIMEOUT_SECS=5
NAVI_FETCH_TRANSIENT_BUDGET_SECS=30
NAVI_FETCH_BACKOFF_INITIAL_SECS=1
NAVI_FETCH_BACKOFF_MAX_SECS=2
NAVI_FETCH_RECOVERY_CLEAN_NEED=1
NAVI_FETCH_RECOVERY_INTERVAL_SECS=1
EOF

# geofabrik: paths so region_md5_url is used (url: sources skip published checksums).
cat >"$PACK/regions.conf" <<EOF
test_ok	geofabrik:ok/extract
test_html	geofabrik:html/extract
test_nocheck	geofabrik:nocheck/extract
EOF

export NAVI_PACK_CONFIG="$PACK/config.env"
export NAVI_PACK_ROOT="$PACK"
export NAVI_REGIONS_CONF="$PACK/regions.conf"
export NAVI_GEOFABRIK_BASE="$BASE"
unset NAVI_EXTRACTS_DIR NAVI_SCRATCH_DIR NAVI_STATE_DIR NAVI_LOG_DIR || true

# Echo exit status only (do not `return` non-zero under set -e).
run_fetch() {
  local rid="$1" out="$2"
  set +e
  env NAVI_PACK_CONFIG="$PACK/config.env" \
      NAVI_PACK_ROOT="$PACK" \
      NAVI_REGIONS_CONF="$PACK/regions.conf" \
      NAVI_GEOFABRIK_BASE="$BASE" \
    "${SCRIPT_DIR}/fetch-extracts.sh" --force "$rid" >"$out" 2>&1
  local rc=$?
  set -e
  printf '%s\n' "$rc"
}

echo "=== happy path: binary PBF + matching checksum ==="
rc="$(run_fetch test_ok "$TMP/out_ok.txt")"
pbf_ok="$PACK/scratch/extracts/test_ok-latest.osm.pbf"
if [[ "$rc" -eq 0 && -s "$pbf_ok" ]] && rg -q 'checksum OK' "$TMP/out_ok.txt"; then
  echo "PASS happy path fetch+checksum"
  PASS=$((PASS + 1))
else
  echo "FAIL happy path rc=$rc"
  cat "$TMP/out_ok.txt"
  FAIL=$((FAIL + 1))
fi

echo "=== HTML soft-404 (200 + HTML body) must die; no PBF left ==="
rc="$(run_fetch test_html "$TMP/out_html.txt")"
pbf_html="$PACK/scratch/extracts/test_html-latest.osm.pbf"
if [[ "$rc" -ne 0 ]] && rg -q 'HTML not PBF|redirect/soft-404' "$TMP/out_html.txt" && [[ ! -f "$pbf_html" ]]; then
  echo "PASS HTML soft-404 rejected and PBF absent"
  PASS=$((PASS + 1))
else
  echo "FAIL HTML soft-404 rc=$rc exists=$([[ -f $pbf_html ]] && echo yes || echo no)"
  cat "$TMP/out_html.txt"
  FAIL=$((FAIL + 1))
fi

echo "=== checksum URL 404 must die; unverified PBF deleted ==="
rc="$(run_fetch test_nocheck "$TMP/out_nocheck.txt")"
pbf_nc="$PACK/scratch/extracts/test_nocheck-latest.osm.pbf"
if [[ "$rc" -ne 0 ]] && rg -q 'checksum URL failed|refusing unverified' "$TMP/out_nocheck.txt" && [[ ! -f "$pbf_nc" ]]; then
  echo "PASS checksum URL failure deletes unverified PBF"
  PASS=$((PASS + 1))
else
  echo "FAIL checksum URL failure rc=$rc exists=$([[ -f $pbf_nc ]] && echo yes || echo no)"
  cat "$TMP/out_nocheck.txt"
  FAIL=$((FAIL + 1))
fi

if ! rg -q 'leaving PBF but flagging' "$TMP/out_nocheck.txt" "$TMP/out_html.txt"; then
  echo "PASS no soft-continue 'leaving PBF but flagging' on fail paths"
  PASS=$((PASS + 1))
else
  echo "FAIL soft-continue wording still present"
  FAIL=$((FAIL + 1))
fi

echo "=== summary pass=$PASS fail=$FAIL ==="
[[ "$FAIL" -eq 0 ]]
