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
#   sudo /media/navi/navi-server/scripts/setup-server.sh --apply-ddns
#   sudo /media/navi/navi-server/scripts/setup-server.sh --apply-datex
#   /media/navi/navi-server/scripts/setup-server.sh --check
#   /media/navi/navi-server/scripts/setup-server.sh --dry-run
# Uninstall helpers:
#   sudo /media/navi/navi-server/scripts/uninstall-ddns.sh [--purge]
#   sudo /media/navi/navi-server/scripts/uninstall-datex-npra.sh [--purge]

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
DDNS_UNIT_SRC="${NAVI_SERVER_ROOT}/systemd/navi-ddns.service"
DDNS_TIMER_SRC="${NAVI_SERVER_ROOT}/systemd/navi-ddns.timer"
DDNS_ENV_EXAMPLE="${NAVI_SERVER_ROOT}/scripts/ddns.env.example"
DDNS_ENV="${DATA}/ddns.env"
DATEX_UNIT_SRC="${NAVI_SERVER_ROOT}/systemd/navi-datex-npra.service"
DATEX_TIMER_SRC="${NAVI_SERVER_ROOT}/systemd/navi-datex-npra.timer"
DATEX_SECRETS="${DATA}/secrets/datex_npra.env"
SYSTEMD_DIR="/etc/systemd/system"

APPLY_APACHE=0
APPLY_SERVICE=0
APPLY_DDNS=0
APPLY_DATEX=0
CHECK_ONLY=0
DRY_RUN=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply-apache) APPLY_APACHE=1; shift ;;
    --apply-service) APPLY_SERVICE=1; shift ;;
    --apply-ddns) APPLY_DDNS=1; shift ;;
    --apply-datex) APPLY_DATEX=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "INFO" "$*"; }
warn() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "WARN" "$*"; }
fail() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "FAILED" "$*"; exit 1; }

LANDING_SRC="${NAVI_SERVER_ROOT}/http/index.html"
LANDING_DST="${DATA}/published/index.html"

# Always overwrite — landing page is repo-owned (http/index.html), not
# operator-customized. Edit the source under http/ and re-run setup.
install_published_landing() {
  if [[ ! -f "$LANDING_SRC" ]]; then
    warn "missing landing page source ${LANDING_SRC}"
    return 0
  fi
  mkdir -p "${DATA}/published"
  cp "$LANDING_SRC" "$LANDING_DST"
  chmod a+r "$LANDING_DST"
  log "installed published landing page ${LANDING_DST} (from http/index.html; setup always overwrites)"
}

# Print what interactive DATEX / full setup would do — no writes, no systemctl.
if [[ "$DRY_RUN" -eq 1 ]]; then
  cat <<EOF
== setup-server.sh dry-run (no changes) ==
root: ${NAVI_SERVER_ROOT}
data: ${DATA}

Would install static landing page:
  ${LANDING_SRC}
    -> ${LANDING_DST}
  (repo-owned; setup always overwrites; no Apache reload needed for the HTML file)

Full interactive sudo run would:
  1. Enable Apache site apache-navi-packs on :80
  2. Ensure service user ${SERVICE_USER} + bake/scrub systemd units
  3. Offer DATEX (interactive TTY only):
       Prompt: Do you want to set up a DATEX provider? [yes/no]
       If no:  leave NAVI_DATEX_NPRA_ENABLED=0 (no secrets, no timer)
       If yes: Prompt: DATEX username
               Prompt: DATEX password (hidden)
               Prompt: Confirm password (hidden)
               Write:  ${DATEX_SECRETS} (mode 0600; NAV_DATEX_USERNAME / NAV_DATEX_PASSWORD)
               Set:    NAVI_DATEX_NPRA_ENABLED=1 in ${DATA}/config.env
               Install/enable: navi-datex-npra.timer (+ one poll start)

--apply-datex alone would run only step 3 (same prompts).

File edit instead of prompts (no interactive setup required):
  See README.md "DATEX NPRA" — create ${DATEX_SECRETS} and set
  NAVI_DATEX_NPRA_ENABLED=1 in ${DATA}/config.env, then enable the timer.

Current flags that would apply if not --dry-run:
  APPLY_APACHE=${APPLY_APACHE} APPLY_SERVICE=${APPLY_SERVICE}
  APPLY_DDNS=${APPLY_DDNS} APPLY_DATEX=${APPLY_DATEX}
EOF
  exit 0
fi

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

# Install + enable Dynamic DNS timer (requires data/ddns.env credentials).
ddns_apply() {
  require_root "--apply-ddns"
  command -v systemctl >/dev/null 2>&1 || fail "systemctl not found"
  command -v curl >/dev/null 2>&1 || fail "curl not found (required for DDNS)"
  [[ -f "$DDNS_UNIT_SRC" ]] || fail "missing ${DDNS_UNIT_SRC}"
  [[ -f "$DDNS_TIMER_SRC" ]] || fail "missing ${DDNS_TIMER_SRC}"
  [[ -x "${NAVI_SERVER_ROOT}/scripts/ddns-update.sh" ]] \
    || fail "missing executable ${NAVI_SERVER_ROOT}/scripts/ddns-update.sh"

  if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    fail "service user ${SERVICE_USER} missing — run --apply-service first"
  fi

  if [[ ! -f "$DDNS_ENV" ]]; then
    [[ -f "$DDNS_ENV_EXAMPLE" ]] || fail "missing ${DDNS_ENV_EXAMPLE}"
    cp "$DDNS_ENV_EXAMPLE" "$DDNS_ENV"
    chown "${SERVICE_USER}:${SERVICE_USER}" "$DDNS_ENV"
    chmod 600 "$DDNS_ENV"
    fail "wrote ${DDNS_ENV} from example — edit hostname/token, then re-run --apply-ddns"
  fi
  chown "${SERVICE_USER}:${SERVICE_USER}" "$DDNS_ENV"
  chmod 600 "$DDNS_ENV"

  # shellcheck disable=SC1090
  set -a
  # shellcheck source=/dev/null
  source "$DDNS_ENV"
  set +a
  [[ -n "${NAVI_DDNS_PROVIDER:-}" ]] || fail "NAVI_DDNS_PROVIDER empty in ${DDNS_ENV}"
  [[ -n "${NAVI_DDNS_HOSTNAME:-}" ]] || fail "NAVI_DDNS_HOSTNAME empty in ${DDNS_ENV}"
  case "${NAVI_DDNS_PROVIDER}" in
    duckdns|cloudflare)
      [[ -n "${NAVI_DDNS_TOKEN:-}" ]] || fail "NAVI_DDNS_TOKEN empty in ${DDNS_ENV}"
      ;;
    url)
      [[ -n "${NAVI_DDNS_UPDATE_URL:-}" ]] || fail "NAVI_DDNS_UPDATE_URL empty in ${DDNS_ENV}"
      ;;
    *) fail "unsupported NAVI_DDNS_PROVIDER=${NAVI_DDNS_PROVIDER} (duckdns|cloudflare|url)" ;;
  esac

  mkdir -p "${DATA}/state"
  chown "${SERVICE_USER}:${SERVICE_USER}" "${DATA}/state"

  install -m 0644 "$DDNS_UNIT_SRC" "${SYSTEMD_DIR}/navi-ddns.service"
  install -m 0644 "$DDNS_TIMER_SRC" "${SYSTEMD_DIR}/navi-ddns.timer"
  systemctl daemon-reload
  systemctl enable --now navi-ddns.timer
  # Immediate refresh (does not wait for OnBootSec).
  systemctl start navi-ddns.service || warn "initial ddns update failed — check journalctl -u navi-ddns.service"
  log "enabled Dynamic DNS: navi-ddns.timer (provider=${NAVI_DDNS_PROVIDER} host=${NAVI_DDNS_HOSTNAME})"
  log "uninstall with: sudo ${NAVI_SERVER_ROOT}/scripts/uninstall-ddns.sh [--purge]"
}

# Interactive DATEX NPRA enable: yes/no, then username + password.
# Writes secrets (0600), flips NAVI_DATEX_NPRA_ENABLED=1, installs poll timer.
# Other settings use defaults from config.example.env (edit config.env later).
# Called from full sudo setup and from --apply-datex.
datex_apply() {
  require_root "DATEX setup"
  command -v systemctl >/dev/null 2>&1 || fail "systemctl not found"
  command -v python3 >/dev/null 2>&1 || fail "python3 not found"
  [[ -f "$DATEX_UNIT_SRC" ]] || fail "missing ${DATEX_UNIT_SRC}"
  [[ -f "$DATEX_TIMER_SRC" ]] || fail "missing ${DATEX_TIMER_SRC}"
  [[ -x "${NAVI_SERVER_ROOT}/scripts/datex-npra-poll.sh" ]] \
    || fail "missing executable ${NAVI_SERVER_ROOT}/scripts/datex-npra-poll.sh"
  if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    fail "service user ${SERVICE_USER} missing — run --apply-service first"
  fi
  [[ -t 0 ]] || fail "DATEX setup requires an interactive TTY (yes/no + credentials)"

  # Do not record interactive answers in shell history (passwords especially).
  set +o history 2>/dev/null || true

  echo
  echo "=== DATEX provider (optional) ==="
  echo "Credentials stay on this host; Navi clients only GET cached XML."
  echo "NPRA access: https://www.vegvesen.no/en/fag/technology/open-data/..."
  echo

  local ans want_datex=0
  while true; do
    read -r -p "Do you want to set up a DATEX provider? [yes/no]: " ans
    case "${ans,,}" in
      y|yes)
        want_datex=1
        break
        ;;
      n|no)
        want_datex=0
        break
        ;;
      *)
        echo "Please answer yes or no."
        ;;
    esac
  done

  if [[ "$want_datex" -eq 0 ]]; then
    log "DATEX provider skipped (left disabled)"
    return 0
  fi

  local username password password2
  local poll_secs=300
  local endpoints="GetSituation,GetTravelTimeData,GetMeasuredWeatherData,GetCCTVSiteTable"
  local base_url="https://datex-server-get-v3-1.atlas.vegvesen.no"
  # Identifying contact for upstream User-Agent — edit NAVI_DATEX_NPRA_USER_AGENT
  # in data/config.env if you need a real email/URL (NPRA/MET-style rules).
  local contact="REPLACE_WITH_YOUR_EMAIL_OR_URL"

  read -r -p "DATEX username: " username
  [[ -n "$username" ]] || fail "username required"
  # -s: no terminal echo. Values are never passed on argv or written to setup logs.
  read -r -s -p "DATEX password: " password
  echo
  read -r -s -p "Confirm password: " password2
  echo
  [[ "$password" == "$password2" ]] || fail "passwords do not match"
  [[ -n "$password" ]] || fail "password required"
  password2=""

  mkdir -p "${DATA}/secrets" "${DATA}/datex_npra/state" "${DATA}/datex_npra/cache"
  # Password via stdin only — never argv, never setup log(), never env.
  # Username is argv (not secret); password is piped so it is not in python argv.
  printf '%s' "$password" | python3 -c '
import sys
from pathlib import Path
path = Path(sys.argv[1])
user = sys.argv[2]
password = sys.stdin.read()
path.parent.mkdir(parents=True, exist_ok=True)
text = (
    "# Written by setup-server.sh DATEX setup — mode 0600. Do not commit.\n"
    f"NAV_DATEX_USERNAME={user}\n"
    f"NAV_DATEX_PASSWORD={password}\n"
)
path.write_text(text, encoding="utf-8")
path.chmod(0o600)
' "$DATEX_SECRETS" "$username"
  password=""
  chown "${SERVICE_USER}:${SERVICE_USER}" "$DATEX_SECRETS"
  chmod 600 "$DATEX_SECRETS"

  if [[ ! -f "${DATA}/config.env" ]]; then
    cp "${NAVI_SERVER_ROOT}/scripts/config.example.env" "${DATA}/config.env"
  fi

  _datex_set_config() {
    local key="$1" val="$2" file="${DATA}/config.env" line
    # %q so values with spaces/parens source cleanly; never log val.
    printf -v line '%s=%q' "$key" "$val"
    if grep -q "^${key}=" "$file" 2>/dev/null; then
      grep -v "^${key}=" "$file" >"${file}.tmp"
      mv "${file}.tmp" "$file"
    fi
    printf '%s\n' "$line" >>"$file"
  }
  _datex_set_config NAVI_DATEX_NPRA_ENABLED 1
  _datex_set_config NAVI_DATEX_NPRA_BASE_URL "$base_url"
  _datex_set_config NAVI_DATEX_NPRA_ENDPOINTS "$endpoints"
  _datex_set_config NAVI_DATEX_NPRA_POLL_INTERVAL_SECS "$poll_secs"
  _datex_set_config NAVI_DATEX_NPRA_USE_IF_MODIFIED_SINCE 1
  _datex_set_config NAVI_DATEX_NPRA_USER_AGENT "navi-server-datex/0.1 (contact: ${contact})"
  _datex_set_config NAVI_DATEX_NPRA_SECRETS_FILE "$DATEX_SECRETS"

  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${DATA}/secrets" "${DATA}/datex_npra"
  chmod 700 "${DATA}/secrets" "${DATA}/datex_npra"

  install -m 0644 "$DATEX_UNIT_SRC" "${SYSTEMD_DIR}/navi-datex-npra.service"
  install -m 0644 "$DATEX_TIMER_SRC" "${SYSTEMD_DIR}/navi-datex-npra.timer"
  systemctl daemon-reload
  systemctl enable --now navi-datex-npra.timer
  systemctl start navi-datex-npra.service \
    || warn "initial DATEX poll failed — check journalctl -u navi-datex-npra.service (operator only)"
  log "enabled DATEX NPRA poll: navi-datex-npra.timer"
  log "set NAVI_DATEX_NPRA_USER_AGENT contact in ${DATA}/config.env if still a placeholder"
  log "client cache path (after successful poll): ${DATA}/published/datex/"
  log "uninstall: sudo ${NAVI_SERVER_ROOT}/scripts/uninstall-datex-npra.sh [--purge]"
}

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "== navi-server setup check =="
  echo "root: ${NAVI_SERVER_ROOT}"
  [[ -d "${DATA}/published" ]] && echo "OK published/" || echo "MISSING published/"
  if [[ -f "${DATA}/published/index.html" ]]; then
    echo "OK published/index.html landing page"
  else
    echo "MISSING published/index.html (re-run setup-server.sh to install from http/index.html)"
  fi
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
  if [[ -f "${SYSTEMD_DIR}/navi-ddns.service" && -f "${SYSTEMD_DIR}/navi-ddns.timer" ]]; then
    echo "OK ddns units installed"
    if systemctl is-enabled navi-ddns.timer >/dev/null 2>&1; then
      echo "OK navi-ddns.timer enabled ($(systemctl is-enabled navi-ddns.timer))"
    else
      echo "MISSING ddns timer enable (sudo $0 --apply-ddns)"
    fi
  else
    echo "DDNS units not installed (optional: sudo $0 --apply-ddns)"
  fi
  if [[ -f "$DDNS_ENV" ]]; then
    echo "OK ${DDNS_ENV} present (mode=$(stat -c '%a' "$DDNS_ENV" 2>/dev/null || echo '?'))"
  else
    echo "DDNS config absent (optional: cp scripts/ddns.env.example data/ddns.env)"
  fi
  if [[ -f "${SYSTEMD_DIR}/navi-datex-npra.timer" ]]; then
    echo "OK datex units installed (enabled=$(systemctl is-enabled navi-datex-npra.timer 2>/dev/null || echo no))"
  else
    echo "DATEX NPRA units not installed (optional/off-by-default: sudo $0 --apply-datex)"
  fi
  if [[ -f "${DATA}/config.env" ]] && grep -q '^NAVI_DATEX_NPRA_ENABLED=1' "${DATA}/config.env" 2>/dev/null; then
    echo "OK DATEX feature flag ENABLED in config.env"
  else
    echo "OK DATEX feature flag off/absent (inert default)"
  fi
  if command -v apache2ctl >/dev/null 2>&1; then
    apache2ctl -S 2>&1 | grep -E 'navi-packs|\*:80' || true
  fi
  code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/current.json || true)"
  echo "GET /current.json -> ${code}"
  root_code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/ || true)"
  echo "GET / -> ${root_code} (want 200 when published/index.html is installed)"
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

install_published_landing

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

if [[ "$APPLY_DDNS" -eq 1 ]]; then
  ddns_apply
fi

if [[ "$APPLY_DATEX" -eq 1 ]]; then
  datex_apply
fi

if [[ "$APPLY_APACHE" -eq 1 ]]; then
  require_root "--apply-apache"
  apache_apply
elif [[ "$(id -u)" -eq 0 && "$APPLY_SERVICE" -eq 0 && "$APPLY_DDNS" -eq 0 && "$APPLY_DATEX" -eq 0 ]]; then
  # Full sudo run without flags: configure Apache + service user, then offer DATEX.
  apache_apply
  navit_server_apply
  if [[ -t 0 ]]; then
    datex_apply
  else
    log "DATEX left disabled (non-interactive; re-run with --apply-datex on a TTY to enable)"
  fi
elif [[ "$(id -u)" -ne 0 && "$APPLY_SERVICE" -eq 0 && "$APPLY_APACHE" -eq 0 && "$APPLY_DDNS" -eq 0 && "$APPLY_DATEX" -eq 0 ]]; then
  cat <<EOF

Apache / dedicated service user / optional plugins are not configured by this non-root run. Apply with:

  sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --apply-apache
  sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --apply-service
  # Optional Dynamic DNS (edit data/ddns.env first):
  sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --apply-ddns
  sudo ${NAVI_SERVER_ROOT}/scripts/uninstall-ddns.sh [--purge]
  # Optional DATEX (asks yes/no, then username + password):
  sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --apply-datex
  sudo ${NAVI_SERVER_ROOT}/scripts/uninstall-datex-npra.sh [--purge]

Or full sudo setup (Apache + service + DATEX yes/no prompt):

  sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh

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
