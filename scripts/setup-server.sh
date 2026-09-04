#!/usr/bin/env bash
# Configure this box to serve Navi indexed packs (static GET/HEAD on port 80).
#
# Safe to re-run. Apache steps need sudo — either run this script with sudo for
# the whole thing, or run it as a normal user and it will print the sudo block.
#
# Usage:
#   /media/navi/navi-server/scripts/setup-server.sh
#   sudo /media/navi/navi-server/scripts/setup-server.sh --apply-apache
#   /media/navi/navi-server/scripts/setup-server.sh --check

set -euo pipefail

NAVI_SERVER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${NAVI_SERVER_ROOT}/data"
NAVI_ROOT="${NAVI_ROOT:-/media/navi/Navi}"
APACHE_SRC="${NAVI_SERVER_ROOT}/http/apache-navi-packs.conf"
APACHE_DST="/etc/apache2/sites-available/apache-navi-packs.conf"

APPLY_APACHE=0
CHECK_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply-apache) APPLY_APACHE=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "INFO" "$*"; }
warn() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "WARN" "$*"; }
fail() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "FAILED" "$*"; exit 1; }

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "== navi-server setup check =="
  echo "root: ${NAVI_SERVER_ROOT}"
  [[ -d "${DATA}/published" ]] && echo "OK published/" || echo "MISSING published/"
  [[ -f "${DATA}/config.env" ]] && echo "OK config.env" || echo "MISSING config.env"
  [[ -f "${DATA}/regions.conf" ]] && echo "OK regions.conf" || echo "MISSING regions.conf"
  if [[ -x "${NAVI_ROOT}/target/release/navi-indexed-convert" ]]; then
    echo "OK convert binary"
  else
    echo "MISSING convert binary (build in ${NAVI_ROOT})"
  fi
  if command -v apache2ctl >/dev/null 2>&1; then
    apache2ctl -S 2>&1 | grep -E 'navi-packs|\*:80' || true
  fi
  code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/current.json || true)"
  echo "GET /current.json -> ${code}"
  post="$(curl -s -o /dev/null -w '%{http_code}' -X POST -d x http://127.0.0.1/current.json || true)"
  echo "POST /current.json -> ${post} (want 403)"
  exit 0
fi

log "navi-server root=${NAVI_SERVER_ROOT}"

# --- directories + config (no root) ---
mkdir -p \
  "${DATA}/scratch/extracts" \
  "${DATA}/scratch/convert" \
  "${DATA}/state" \
  "${DATA}/staging" \
  "${DATA}/generations" \
  "${DATA}/logs" \
  "${DATA}/published/packs"

if [[ ! -f "${DATA}/config.env" ]]; then
  cp "${NAVI_SERVER_ROOT}/scripts/config.example.env" "${DATA}/config.env"
  log "wrote ${DATA}/config.env"
fi
if [[ ! -f "${DATA}/regions.conf" ]]; then
  cp "${NAVI_SERVER_ROOT}/scripts/regions.example.conf" "${DATA}/regions.conf"
  log "wrote ${DATA}/regions.conf"
fi

# www-data must traverse to published/ and read files
chmod o+x /media/navi "${NAVI_SERVER_ROOT}" "${DATA}" 2>/dev/null || true
chmod -R a+rX "${DATA}/published"
log "permissions on published/ OK"

# Stop prototype :8097 listener if present (user unit; no sudo)
if systemctl --user is-active navi-packs-static.service >/dev/null 2>&1; then
  systemctl --user disable --now navi-packs-static.service || true
  log "stopped user unit navi-packs-static (port 8097)"
fi

# --- Apache (sudo) ---
apache_apply() {
  [[ -f "$APACHE_SRC" ]] || fail "missing ${APACHE_SRC}"
  command -v apache2ctl >/dev/null 2>&1 || fail "apache2 not installed"
  cp "$APACHE_SRC" "$APACHE_DST"
  a2dissite 000-default.conf >/dev/null 2>&1 || true
  a2ensite apache-navi-packs >/dev/null
  apache2ctl configtest
  systemctl reload apache2
  log "Apache site apache-navi-packs enabled on :80"
}

if [[ "$APPLY_APACHE" -eq 1 ]]; then
  if [[ "$(id -u)" -ne 0 ]]; then
    fail "--apply-apache requires root (re-run with sudo)"
  fi
  apache_apply
elif [[ "$(id -u)" -eq 0 ]]; then
  apache_apply
else
  cat <<EOF

Apache is not configured by this non-root run. Apply with:

  sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --apply-apache

Or, if you prefer one-liners:

  sudo cp ${APACHE_SRC} ${APACHE_DST}
  sudo a2dissite 000-default.conf
  sudo a2ensite apache-navi-packs
  sudo apache2ctl configtest && sudo systemctl reload apache2

EOF
fi

# --- convert binary hint ---
if [[ ! -x "${NAVI_ROOT}/target/release/navi-indexed-convert" ]]; then
  warn "navi-indexed-convert not built yet. From a login with Rust 1.98+:"
  warn "  . \"\$HOME/.cargo/env\" && cd ${NAVI_ROOT} && cargo build -p navi-ffi --release --bin navi-indexed-convert"
else
  log "convert binary present"
fi

log "setup complete — verify with: ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --check"
log "smoke test example: ${NAVI_SERVER_ROOT}/scripts/run-weekly.sh --region us_west_virginia"
