#!/usr/bin/env bash
# Configure this box to serve Navi indexed packs (static GET/HEAD on port 80).
#
# Safe to re-run. Apache / service-user steps need sudo — either run this
# script with sudo for the whole thing, or run it as a normal user and it will
# print the sudo block.
#
# Usage:
#   /media/navi/navi-server/scripts/setup-server.sh
#   sudo /media/navi/navi-server/scripts/setup-server.sh --apply-apache
#   sudo /media/navi/navi-server/scripts/setup-server.sh --apply-service
#   /media/navi/navi-server/scripts/setup-server.sh --check

set -euo pipefail

NAVI_SERVER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${NAVI_SERVER_ROOT}/data"
CONVERT_BIN="${NAVI_SERVER_ROOT}/target/release/navi-indexed-convert"
APACHE_SRC="${NAVI_SERVER_ROOT}/http/apache-navi-packs.conf"
APACHE_DST="/etc/apache2/sites-available/apache-navi-packs.conf"
SERVICE_USER="${NAVI_SERVICE_USER:-navit-server}"
SERVICE_UNIT_SRC="${NAVI_SERVER_ROOT}/systemd/navit-server.service"
BAKE_UNIT_SRC="${NAVI_SERVER_ROOT}/systemd/navi-pack-bake.service"
BAKE_TIMER_SRC="${NAVI_SERVER_ROOT}/systemd/navi-pack-bake.timer"
SCRUB_UNIT_SRC="${NAVI_SERVER_ROOT}/systemd/navi-pack-scrub.service"
SCRUB_TIMER_SRC="${NAVI_SERVER_ROOT}/systemd/navi-pack-scrub.timer"
SYSTEMD_DIR="/etc/systemd/system"

APPLY_APACHE=0
APPLY_SERVICE=0
CHECK_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply-apache) APPLY_APACHE=1; shift ;;
    --apply-service) APPLY_SERVICE=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "INFO" "$*"; }
warn() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "WARN" "$*"; }
fail() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "FAILED" "$*"; exit 1; }

require_root() {
  local what="$1"
  [[ "$(id -u)" -eq 0 ]] || fail "${what} requires root (re-run with sudo)"
}

# Create dedicated system user + install systemd units so bake jobs run as
# navit-server (not a login account). Enables daily scrub timer (self-maintaining).
# Does NOT enable the weekly bake timer.
navit_server_apply() {
  require_root "--apply-service"
  command -v useradd >/dev/null 2>&1 || fail "useradd not found"
  command -v systemctl >/dev/null 2>&1 || fail "systemctl not found"
  [[ -f "$SERVICE_UNIT_SRC" ]] || fail "missing ${SERVICE_UNIT_SRC}"
  [[ -f "$BAKE_UNIT_SRC" ]] || fail "missing ${BAKE_UNIT_SRC}"
  [[ -f "$SCRUB_UNIT_SRC" ]] || fail "missing ${SCRUB_UNIT_SRC}"
  [[ -f "$SCRUB_TIMER_SRC" ]] || fail "missing ${SCRUB_TIMER_SRC}"

  if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --user-group \
      --home-dir "$NAVI_SERVER_ROOT" \
      --shell /usr/sbin/nologin \
      --comment "Navi pack server" \
      "$SERVICE_USER"
    log "created system user ${SERVICE_USER}"
  else
    log "system user ${SERVICE_USER} already exists"
  fi

  # Runtime data owned by the service user; published stays world-readable for Apache.
  # Skip chown of busy scratch if another bake owns open files — still set top-level.
  mkdir -p \
    "${DATA}/scratch/extracts" \
    "${DATA}/scratch/convert" \
    "${DATA}/state" \
    "${DATA}/staging" \
    "${DATA}/generations" \
    "${DATA}/logs" \
    "${DATA}/published/packs" \
    "${DATA}/elevation/copernicus" \
    "${DATA}/elevation/viewfinder" \
    "${DATA}/elevation/srtm"
  if pgrep -f '/scripts/run-planet-smoke\.sh|/scripts/run-weekly\.sh|navi-indexed-convert' >/dev/null 2>&1; then
    warn "bake/smoke process running — deferring recursive chown of ${DATA} (user created; re-run --apply-service when idle)"
    chown "${SERVICE_USER}:${SERVICE_USER}" "$DATA" 2>/dev/null || true
  else
    chown -R "${SERVICE_USER}:${SERVICE_USER}" "$DATA"
  fi
  chmod o+x /media/navi "$NAVI_SERVER_ROOT" "$DATA" 2>/dev/null || true
  chmod -R a+rX "${DATA}/published"

  # Convert binary must be readable/executable by the service user.
  if [[ -x "$CONVERT_BIN" ]]; then
    chmod o+x /media/navi "$NAVI_SERVER_ROOT" \
      "${NAVI_SERVER_ROOT}/target" "${NAVI_SERVER_ROOT}/target/release" 2>/dev/null || true
    chmod o+rx "$CONVERT_BIN" 2>/dev/null || true
  else
    warn "convert binary not built yet — ${SERVICE_USER} will need execute access after build"
  fi

  install -m 0644 "$SERVICE_UNIT_SRC" "${SYSTEMD_DIR}/navit-server.service"
  install -m 0644 "$BAKE_UNIT_SRC" "${SYSTEMD_DIR}/navi-pack-bake.service"
  if [[ -f "$BAKE_TIMER_SRC" ]]; then
    install -m 0644 "$BAKE_TIMER_SRC" "${SYSTEMD_DIR}/navi-pack-bake.timer"
  fi
  install -m 0644 "$SCRUB_UNIT_SRC" "${SYSTEMD_DIR}/navi-pack-scrub.service"
  install -m 0644 "$SCRUB_TIMER_SRC" "${SYSTEMD_DIR}/navi-pack-scrub.timer"
  systemctl daemon-reload
  # Daily scrub is part of self-maintaining setup; bake timer stays opt-in.
  systemctl enable --now navi-pack-scrub.timer
  log "installed systemd units: navit-server.service navi-pack-bake.service navi-pack-scrub.service (+ timers)"
  log "enabled daily scrub: navi-pack-scrub.timer (systemctl list-timers navi-pack-scrub.timer)"
  log "hand-run bake as service: sudo systemctl start navit-server.service"
  log "weekly bake timer remains disabled until you: sudo systemctl enable --now navi-pack-bake.timer"
}

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "== navi-server setup check =="
  echo "root: ${NAVI_SERVER_ROOT}"
  [[ -d "${DATA}/published" ]] && echo "OK published/" || echo "MISSING published/"
  [[ -f "${DATA}/config.env" ]] && echo "OK config.env" || echo "MISSING config.env"
  [[ -f "${DATA}/regions.conf" ]] && echo "OK regions.conf" || echo "MISSING regions.conf"
  if [[ -x "$CONVERT_BIN" ]]; then
    echo "OK convert binary (${CONVERT_BIN})"
  else
    echo "MISSING convert binary (cd ${NAVI_SERVER_ROOT} && cargo build --release -p navi-indexed-convert)"
  fi
  if id -u "$SERVICE_USER" >/dev/null 2>&1; then
    echo "OK service user ${SERVICE_USER} (uid=$(id -u "$SERVICE_USER"))"
  else
    echo "MISSING service user ${SERVICE_USER} (sudo $0 --apply-service)"
  fi
  if [[ -f "${SYSTEMD_DIR}/navit-server.service" ]]; then
    echo "OK systemd unit navit-server.service installed"
    systemctl is-enabled navit-server.service 2>/dev/null || echo "  (oneshot; enable not required — start manually or via timer)"
  else
    echo "MISSING systemd unit navit-server.service"
  fi
  if [[ -f "${SYSTEMD_DIR}/navi-pack-scrub.service" && -f "${SYSTEMD_DIR}/navi-pack-scrub.timer" ]]; then
    echo "OK scrub units installed"
    if systemctl is-enabled navi-pack-scrub.timer >/dev/null 2>&1; then
      echo "OK navi-pack-scrub.timer enabled ($(systemctl is-enabled navi-pack-scrub.timer))"
    else
      echo "MISSING scrub timer enable (sudo $0 --apply-service)"
    fi
  else
    echo "MISSING scrub units (sudo $0 --apply-service)"
  fi
  if [[ -f "${SYSTEMD_DIR}/navi-pack-bake.timer" ]]; then
    echo "OK bake timer file installed (enabled=$(systemctl is-enabled navi-pack-bake.timer 2>/dev/null || echo no))"
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
  "${DATA}/published/packs" \
  "${DATA}/elevation/copernicus" \
  "${DATA}/elevation/viewfinder" \
  "${DATA}/elevation/srtm"

if [[ ! -f "${DATA}/config.env" ]]; then
  cp "${NAVI_SERVER_ROOT}/scripts/config.example.env" "${DATA}/config.env"
  log "wrote ${DATA}/config.env"
fi
if ! grep -q '^NAVI_SERVICE_USER=' "${DATA}/config.env" 2>/dev/null; then
  printf '\n# Dedicated system user for bake jobs (setup-server.sh --apply-service)\nNAVI_SERVICE_USER=%s\n' \
    "$SERVICE_USER" >>"${DATA}/config.env"
  log "appended NAVI_SERVICE_USER=${SERVICE_USER} to config.env"
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

if [[ "$APPLY_SERVICE" -eq 1 ]]; then
  navit_server_apply
fi

if [[ "$APPLY_APACHE" -eq 1 ]]; then
  require_root "--apply-apache"
  apache_apply
elif [[ "$(id -u)" -eq 0 && "$APPLY_SERVICE" -eq 0 ]]; then
  # Full sudo run without flags: configure Apache + service user.
  apache_apply
  navit_server_apply
elif [[ "$(id -u)" -ne 0 && "$APPLY_SERVICE" -eq 0 ]]; then
  cat <<EOF

Apache / dedicated service user are not configured by this non-root run. Apply with:

  sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --apply-apache
  sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --apply-service

Or both in one sudo:

  sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --apply-apache --apply-service

EOF
fi

# --- convert binary (in-repo pack-convert-core; no Navi tree required) ---
if [[ ! -x "$CONVERT_BIN" ]]; then
  cargo_bin=""
  if command -v cargo >/dev/null 2>&1; then
    cargo_bin="$(command -v cargo)"
  elif [[ -x "${HOME}/.cargo/bin/cargo" ]]; then
    cargo_bin="${HOME}/.cargo/bin/cargo"
  fi
  if [[ -n "$cargo_bin" ]]; then
    log "building navi-indexed-convert (release) in ${NAVI_SERVER_ROOT}"
    (cd "$NAVI_SERVER_ROOT" && CARGO_TARGET_DIR="${NAVI_SERVER_ROOT}/target" \
      "$cargo_bin" build --release -p navi-indexed-convert)
  else
    warn "navi-indexed-convert not built yet and cargo not found. Install Rust, then:"
    warn "  . \"\$HOME/.cargo/env\" && cd ${NAVI_SERVER_ROOT} && cargo build --release -p navi-indexed-convert"
  fi
fi
if [[ -x "$CONVERT_BIN" ]]; then
  log "convert binary present: ${CONVERT_BIN}"
else
  warn "convert binary still missing after setup"
fi

log "setup complete — verify with: ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --check"
log "smoke test example: ${NAVI_SERVER_ROOT}/scripts/run-weekly.sh --region us_west_virginia"
