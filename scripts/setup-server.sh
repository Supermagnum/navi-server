#!/usr/bin/env bash
# Configure this box to serve Navi indexed packs (static GET/HEAD on port 80).
#
# Safe to re-run. Apache / service-user steps need sudo — either run this
# script with sudo for the whole thing, or run it as a normal user and it will
# print the sudo block.
#
# Usage:
#   /media/navi/navi-server/scripts/setup-server.sh
#   /media/navi/navi-server/scripts/setup-server.sh --dry-run
#   /media/navi/navi-server/scripts/setup-server.sh --check
#   sudo …/setup-server.sh --apply-apache
#   sudo …/setup-server.sh --apply-service          # MWM dirs + units; .rkyv frozen by default
#   sudo …/setup-server.sh --apply-service --enable-rkyv-bake  # opt-in weekly .rkyv bake/scrub
#   sudo …/setup-server.sh --apply-mwm              # install MWM check timer (ENABLED=0)
#   sudo …/setup-server.sh --apply-ddns|--apply-datex|--apply-repeaterbook|--apply-radioid|--apply-osm-repeaters
#
# Default on new installs: .rkyv bake/scrub timers installed but disabled; MWM
# check timer enabled (detect-only); NAVI_MWM_ENABLED=0. See docs/mwm-pipeline.md.
# Uninstall helpers:
#   sudo /media/navi/navi-server/scripts/uninstall-ddns.sh [--purge]
#   sudo /media/navi/navi-server/scripts/uninstall-datex-npra.sh [--purge]
#   sudo /media/navi/navi-server/scripts/uninstall-repeaterbook.sh [--purge]
#   sudo /media/navi/navi-server/scripts/uninstall-radioid.sh [--purge]
#   sudo /media/navi/navi-server/scripts/uninstall-osm-repeaters.sh [--purge]

set -euo pipefail

NAVI_SERVER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${NAVI_SERVER_ROOT}/data"
CONVERT_BIN="${NAVI_SERVER_ROOT}/target/release/navi-indexed-convert"
APACHE_SRC="${NAVI_SERVER_ROOT}/http/apache-navi-packs.conf"
APACHE_DST="/etc/apache2/sites-available/apache-navi-packs.conf"
DATEX_REWRITE_SRC="${NAVI_SERVER_ROOT}/http/apache-navi-datex-rewrites.conf"
DATEX_REWRITE_DST="/etc/apache2/conf-available/apache-navi-datex-rewrites.conf"
NAVI_PACKS_COMMON_CONF="/etc/apache2/conf-available/navi-packs-common.conf"
SERVICE_USER="${NAVI_SERVICE_USER:-navit-server}"
SERVICE_UNIT_SRC="${NAVI_SERVER_ROOT}/systemd/navit-server.service"
BAKE_UNIT_SRC="${NAVI_SERVER_ROOT}/systemd/navi-pack-bake.service"
BAKE_TIMER_SRC="${NAVI_SERVER_ROOT}/systemd/navi-pack-bake.timer"
SCRUB_UNIT_SRC="${NAVI_SERVER_ROOT}/systemd/navi-pack-scrub.service"
SCRUB_TIMER_SRC="${NAVI_SERVER_ROOT}/systemd/navi-pack-scrub.timer"
MWM_CHECK_UNIT_SRC="${NAVI_SERVER_ROOT}/systemd/navi-mwm-check.service"
MWM_CHECK_TIMER_SRC="${NAVI_SERVER_ROOT}/systemd/navi-mwm-check.timer"
MWM_BAKE_UNIT_SRC="${NAVI_SERVER_ROOT}/systemd/navi-mwm-bake.service"
MWM_BAKE_TIMER_SRC="${NAVI_SERVER_ROOT}/systemd/navi-mwm-bake.timer"
MWM_SIDECAR_BIN="${NAVI_SERVER_ROOT}/target/release/navi-mwm-sidecar"
MWM_VENV="${NAVI_SERVER_ROOT}/.venv-mwm"
MWM_REQUIREMENTS="${NAVI_SERVER_ROOT}/scripts/mwm/requirements.txt"
MWM_ENV_DST="/etc/navi/mwm.env"
DDNS_UNIT_SRC="${NAVI_SERVER_ROOT}/systemd/navi-ddns.service"
DDNS_TIMER_SRC="${NAVI_SERVER_ROOT}/systemd/navi-ddns.timer"
DDNS_ENV_EXAMPLE="${NAVI_SERVER_ROOT}/scripts/ddns.env.example"
DDNS_ENV="${DATA}/ddns.env"
DATEX_UNIT_SRC="${NAVI_SERVER_ROOT}/systemd/navi-datex-npra.service"
DATEX_TIMER_SRC="${NAVI_SERVER_ROOT}/systemd/navi-datex-npra.timer"
DATEX_SECRETS="${DATA}/secrets/datex_npra.env"
RB_UNIT_SRC="${NAVI_SERVER_ROOT}/systemd/navi-repeaterbook.service"
RB_TIMER_SRC="${NAVI_SERVER_ROOT}/systemd/navi-repeaterbook.timer"
RB_SECRETS="${DATA}/secrets/repeaterbook.env"
RID_SECRETS="${DATA}/secrets/radioid.env"
SYSTEMD_DIR="/etc/systemd/system"

APPLY_APACHE=0
APPLY_SERVICE=0
APPLY_MWM=0
APPLY_DDNS=0
APPLY_DATEX=0
APPLY_REPEATERBOOK=0
APPLY_RADIOID=0
APPLY_OSM_REPEATERS=0
CHECK_ONLY=0
DRY_RUN=0
# Default: .rkyv bake/scrub stay disabled (serving-only / new installs).
RKYV_FROZEN=1
ENABLE_RKYV_BAKE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply-apache) APPLY_APACHE=1; shift ;;
    --apply-service) APPLY_SERVICE=1; shift ;;
    --apply-mwm) APPLY_MWM=1; shift ;;
    --apply-ddns) APPLY_DDNS=1; shift ;;
    --apply-datex) APPLY_DATEX=1; shift ;;
    --apply-repeaterbook) APPLY_REPEATERBOOK=1; shift ;;
    --apply-radioid) APPLY_RADIOID=1; shift ;;
    --apply-osm-repeaters) APPLY_OSM_REPEATERS=1; shift ;;
    --rkyv-frozen) RKYV_FROZEN=1; shift ;;
    --no-rkyv-frozen) RKYV_FROZEN=0; shift ;;
    --enable-rkyv-bake) ENABLE_RKYV_BAKE=1; RKYV_FROZEN=0; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help)
      sed -n '2,28p' "$0"
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
RKYV_FROZEN=${RKYV_FROZEN} ENABLE_RKYV_BAKE=${ENABLE_RKYV_BAKE}

Would ensure directories:
  published/mwm (+ .navi-mwm-owned), published/packs (optional .rkyv),
  elevation/mapterhorn-cache, elevation/geometry-hash-cache,
  state/ (pack-pipeline.lock), logs/, scratch/, .venv-mwm/

Would build (when cargo present):
  navi-indexed-convert, navi-mwm-sidecar
  optional: scripts/mwm/build-generator.sh (routes_builder_tool) — not required for serve

Would install Python venv at ${MWM_VENV} from ${MWM_REQUIREMENTS}

Full interactive sudo run would:
  1. Enable Apache site apache-navi-packs on :80 (DocumentRoot serves /mwm/ and /packs/ if present)
  2. Ensure service user ${SERVICE_USER} + systemd units:
     - navi-mwm-check.timer enabled (detect-only; NAVI_MWM_ENABLED=0)
     - navi-mwm-bake.service installed, timer not enabled
     - navi-pack-bake/scrub units installed; timers DISABLED when RKYV_FROZEN=1 (default)
  3. Write ${MWM_ENV_DST} with NAVI_MWM_ENABLED=0 + cache/concurrency defaults
  4. If RKYV_FROZEN=1: write published/.navi-rkyv-frozen
  5. Offer DATEX (interactive TTY only) — unchanged

--apply-mwm: install MWM units + /etc/navi/mwm.env + dirs (no Apache).
--enable-rkyv-bake: enable weekly .rkyv bake + daily scrub (not default).

Current flags that would apply if not --dry-run:
  APPLY_APACHE=${APPLY_APACHE} APPLY_SERVICE=${APPLY_SERVICE} APPLY_MWM=${APPLY_MWM}
  APPLY_DDNS=${APPLY_DDNS} APPLY_DATEX=${APPLY_DATEX}
  APPLY_REPEATERBOOK=${APPLY_REPEATERBOOK}
  APPLY_RADIOID=${APPLY_RADIOID}
  APPLY_OSM_REPEATERS=${APPLY_OSM_REPEATERS}

--apply-osm-repeaters would (non-interactive):
  Require osmium + zstd; set NAVI_OSM_REPEATERS_ENABLED=1
  Create data/osm_repeaters + published/repeaters (no secrets; ODbL)
EOF
  exit 0
fi

require_root() {
  local what="$1"
  [[ "$(id -u)" -eq 0 ]] || fail "${what} requires root (re-run with sudo)"
}

ensure_mwm_dirs() {
  mkdir -p \
    "${DATA}/published/mwm" \
    "${DATA}/published/packs" \
    "${DATA}/elevation/mapterhorn-cache" \
    "${DATA}/elevation/geometry-hash-cache" \
    "${DATA}/elevation/copernicus" \
    "${DATA}/elevation/viewfinder" \
    "${DATA}/elevation/srtm" \
    "${DATA}/scratch/extracts" \
    "${DATA}/scratch/convert" \
    "${DATA}/state" \
    "${DATA}/staging" \
    "${DATA}/generations" \
    "${DATA}/logs"
  : >>"${DATA}/state/pack-pipeline.lock"
  if [[ ! -f "${DATA}/published/mwm/.navi-mwm-owned" ]]; then
    cat >"${DATA}/published/mwm/.navi-mwm-owned" <<'EOF'
owned-by=navi-mwm-pipeline
do-not-touch-from=rkyv-scrub,cleanup,publish-packs,migrate-server
EOF
    log "wrote ${DATA}/published/mwm/.navi-mwm-owned"
  fi
  if [[ "$RKYV_FROZEN" -eq 1 ]]; then
    if [[ ! -f "${DATA}/published/.navi-rkyv-frozen" ]]; then
      cat >"${DATA}/published/.navi-rkyv-frozen" <<EOF
frozen_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
reason=setup-server default (.rkyv serving-only; MWM is primary bake path)
EOF
      log "wrote ${DATA}/published/.navi-rkyv-frozen"
    fi
  fi
}

write_mwm_env() {
  mkdir -p /etc/navi
  if [[ ! -f "$MWM_ENV_DST" ]]; then
    cat >"$MWM_ENV_DST" <<EOF
# Written by setup-server.sh — override as needed.
NAVI_MWM_ENABLED=0
NAVI_MWM_MODE=comaps-release
NAVI_MWM_PUBLISHED_ROOT=${DATA}/published/mwm
NAVI_MWM_LOCK=${DATA}/state/pack-pipeline.lock
NAVI_MWM_MAPTERHORN_CACHE=${DATA}/elevation/mapterhorn-cache
NAVI_MWM_MAPTERHORN_CACHE_MAX_BYTES=58755104232
NAVI_MWM_GEOMETRY_CACHE=${DATA}/elevation/geometry-hash-cache
NAVI_MWM_PROCESS_CONCURRENCY=0
NAVI_MWM_RAM_HEADROOM_GIB=16
NAVI_MWM_ANNOTATE_CONCURRENCY=2
NAVI_MWM_TILE_FETCH_CONCURRENCY=16
NAVI_MWM_PREFETCH_DISK_BUDGET_GIB=20
NAVI_MWM_DOWNLOAD_CONCURRENCY=2
NAVI_MWM_GRACE_DAYS=7
NAVI_MWM_KEEP_RELEASES=2
EOF
    chmod 644 "$MWM_ENV_DST"
    log "wrote ${MWM_ENV_DST} (NAVI_MWM_ENABLED=0)"
  else
    log "${MWM_ENV_DST} already present — left unchanged"
  fi
}

ensure_mwm_venv() {
  if [[ ! -f "$MWM_REQUIREMENTS" ]]; then
    warn "missing ${MWM_REQUIREMENTS} — skip venv"
    return 0
  fi
  if [[ ! -x "${MWM_VENV}/bin/python" ]]; then
    log "creating Python venv ${MWM_VENV}"
    python3 -m venv "$MWM_VENV"
  fi
  # shellcheck disable=SC1091
  "${MWM_VENV}/bin/pip" install -q -r "$MWM_REQUIREMENTS" || \
    warn "pip install into ${MWM_VENV} failed — install deps manually"
  log "MWM venv ready: ${MWM_VENV}"
}

# Install MWM systemd units + env. Check timer enabled (detect-only); bake timer off.
mwm_units_apply() {
  require_root "--apply-mwm/--apply-service"
  command -v systemctl >/dev/null 2>&1 || fail "systemctl not found"
  [[ -f "$MWM_CHECK_UNIT_SRC" ]] || fail "missing ${MWM_CHECK_UNIT_SRC}"
  [[ -f "$MWM_CHECK_TIMER_SRC" ]] || fail "missing ${MWM_CHECK_TIMER_SRC}"
  [[ -f "$MWM_BAKE_UNIT_SRC" ]] || fail "missing ${MWM_BAKE_UNIT_SRC}"
  ensure_mwm_dirs
  write_mwm_env
  install -m 0644 "$MWM_CHECK_UNIT_SRC" "${SYSTEMD_DIR}/navi-mwm-check.service"
  install -m 0644 "$MWM_CHECK_TIMER_SRC" "${SYSTEMD_DIR}/navi-mwm-check.timer"
  install -m 0644 "$MWM_BAKE_UNIT_SRC" "${SYSTEMD_DIR}/navi-mwm-bake.service"
  if [[ -f "$MWM_BAKE_TIMER_SRC" ]]; then
    install -m 0644 "$MWM_BAKE_TIMER_SRC" "${SYSTEMD_DIR}/navi-mwm-bake.timer"
  fi
  systemctl daemon-reload
  systemctl enable --now navi-mwm-check.timer
  systemctl disable navi-mwm-bake.timer 2>/dev/null || true
  log "enabled navi-mwm-check.timer (detect-only while NAVI_MWM_ENABLED=0)"
  log "navi-mwm-bake.timer left disabled (hand-start bake.service or enable when ready)"
}

# Create dedicated system user + install systemd units.
# Default: .rkyv bake/scrub installed but disabled; MWM check timer on.
navit_server_apply() {
  require_root "--apply-service"
  command -v useradd >/dev/null 2>&1 || fail "useradd not found"
  command -v systemctl >/dev/null 2>&1 || fail "systemctl not found"
  [[ -f "$SERVICE_UNIT_SRC" ]] || fail "missing ${SERVICE_UNIT_SRC}"
  [[ -f "$BAKE_UNIT_SRC" ]] || fail "missing ${BAKE_UNIT_SRC}"
  [[ -f "$BAKE_TIMER_SRC" ]] || fail "missing ${BAKE_TIMER_SRC}"
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

  ensure_mwm_dirs
  write_mwm_env

  if pgrep -f '/scripts/run-planet-smoke\.sh|/scripts/run-weekly\.sh|navi-indexed-convert|run-mwm-bake|run-comaps-release' >/dev/null 2>&1; then
    warn "bake process running — deferring recursive chown of ${DATA}"
    chown "${SERVICE_USER}:${SERVICE_USER}" "$DATA" 2>/dev/null || true
  else
    chown -R "${SERVICE_USER}:${SERVICE_USER}" "$DATA"
  fi
  chmod o+x /media/navi "$NAVI_SERVER_ROOT" "$DATA" 2>/dev/null || true
  chmod -R a+rX "${DATA}/published"

  if [[ -x "$CONVERT_BIN" ]]; then
    chmod o+x /media/navi "$NAVI_SERVER_ROOT" \
      "${NAVI_SERVER_ROOT}/target" "${NAVI_SERVER_ROOT}/target/release" 2>/dev/null || true
    chmod o+rx "$CONVERT_BIN" 2>/dev/null || true
  else
    warn "convert binary not built yet — ${SERVICE_USER} will need execute access after build"
  fi
  if [[ -x "$MWM_SIDECAR_BIN" ]]; then
    chmod o+rx "$MWM_SIDECAR_BIN" 2>/dev/null || true
  fi

  install -m 0644 "$SERVICE_UNIT_SRC" "${SYSTEMD_DIR}/navit-server.service"
  install -m 0644 "$BAKE_UNIT_SRC" "${SYSTEMD_DIR}/navi-pack-bake.service"
  install -m 0644 "$BAKE_TIMER_SRC" "${SYSTEMD_DIR}/navi-pack-bake.timer"
  install -m 0644 "$SCRUB_UNIT_SRC" "${SYSTEMD_DIR}/navi-pack-scrub.service"
  install -m 0644 "$SCRUB_TIMER_SRC" "${SYSTEMD_DIR}/navi-pack-scrub.timer"
  mwm_units_apply

  systemctl daemon-reload
  if [[ "$ENABLE_RKYV_BAKE" -eq 1 ]]; then
    systemctl enable --now navi-pack-scrub.timer
    systemctl enable --now navi-pack-bake.timer
    log "enabled .rkyv scrub + bake timers (--enable-rkyv-bake)"
  else
    systemctl disable --now navi-pack-scrub.timer 2>/dev/null || true
    systemctl disable --now navi-pack-bake.timer 2>/dev/null || true
    log ".rkyv bake/scrub timers installed but disabled (RKYV_FROZEN=${RKYV_FROZEN}; use --enable-rkyv-bake to opt in)"
  fi
  log "installed systemd units: navit-server + pack bake/scrub + mwm check/bake"
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

# Migrate legacy flat published/datex/*.xml|source.json into datex/npra/,
# ensure npra/ exists, and refresh providers.json (no secrets).
datex_migrate_publish_layout() {
  local flat="${DATA}/published/datex"
  local npra="${flat}/npra"
  local f
  mkdir -p "$npra"
  for f in GetSituation.xml GetTravelTimeData.xml GetMeasuredWeatherData.xml \
           GetCCTVSiteTable.xml source.json; do
    if [[ -f "${flat}/${f}" && ! -e "${npra}/${f}" ]]; then
      mv "${flat}/${f}" "${npra}/${f}"
      log "migrated published/datex/${f} -> datex/npra/${f}"
    elif [[ -f "${flat}/${f}" && -e "${npra}/${f}" ]]; then
      rm -f "${flat}/${f}"
      log "removed leftover flat published/datex/${f} (npra copy present)"
    fi
  done
  if [[ -d "${NAVI_SERVER_ROOT}/plugins/datex_common" ]]; then
    PYTHONPATH="${NAVI_SERVER_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
      NAVI_PACK_ROOT="${DATA}" \
      python3 -c '
import os
from pathlib import Path
from plugins.datex_common.providers_index import rebuild_providers_index
root = Path(os.environ["NAVI_PACK_ROOT"])
out = rebuild_providers_index(root)
print(out)
' >/dev/null && log "refreshed ${flat}/providers.json" \
      || warn "could not refresh providers.json (plugins/datex_common missing or failed)"
  fi
  if id -u "$SERVICE_USER" >/dev/null 2>&1; then
    chown -R "${SERVICE_USER}:${SERVICE_USER}" "$flat" 2>/dev/null || true
  fi
  chmod -R a+rX "$flat" 2>/dev/null || true
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
  echo "Canonical client path: /datex/npra/ (legacy flat /datex/* redirects)."
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
    datex_migrate_publish_layout
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

  mkdir -p "${DATA}/secrets" "${DATA}/datex_npra/state" "${DATA}/datex_npra/cache" \
    "${DATA}/published/datex/npra"
  datex_migrate_publish_layout
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
  datex_migrate_publish_layout
  if command -v apache2ctl >/dev/null 2>&1 && [[ -f "$DATEX_REWRITE_SRC" ]]; then
    cp "$DATEX_REWRITE_SRC" "$DATEX_REWRITE_DST"
    ensure_datex_rewrite_include
    [[ -f "$APACHE_SRC" ]] && cp "$APACHE_SRC" "$APACHE_DST"
    apache2ctl configtest && systemctl reload apache2 \
      || warn "Apache DATEX rewrite install failed — run --apply-apache"
  fi
  log "enabled DATEX NPRA poll: navi-datex-npra.timer"
  log "set NAVI_DATEX_NPRA_USER_AGENT contact in ${DATA}/config.env if still a placeholder"
  log "client cache path (after successful poll): ${DATA}/published/datex/npra/"
  log "uninstall: sudo ${NAVI_SERVER_ROOT}/scripts/uninstall-datex-npra.sh [--purge]"
}

# Interactive RepeaterBook mirror enable. Does NOT set REPEATERBOOK_PERMISSION_CONFIRMED.
repeaterbook_apply() {
  require_root "RepeaterBook setup"
  command -v python3 >/dev/null 2>&1 || fail "python3 not found"
  command -v zstd >/dev/null 2>&1 || fail "zstd not found (apt install zstd)"
  [[ -f "$RB_UNIT_SRC" ]] || fail "missing ${RB_UNIT_SRC}"
  [[ -f "$RB_TIMER_SRC" ]] || fail "missing ${RB_TIMER_SRC}"
  [[ -x "${NAVI_SERVER_ROOT}/scripts/repeaterbook-mirror.sh" ]] \
    || fail "missing executable ${NAVI_SERVER_ROOT}/scripts/repeaterbook-mirror.sh"
  [[ -t 0 ]] || fail "RepeaterBook setup requires an interactive TTY (token + identity prompts)"

  echo "=== RepeaterBook mirror (optional) ==="
  echo "Requires written permission from RepeaterBook before any fetch."
  echo "This setup writes the app_ token and enables the timer, but leaves"
  echo "REPEATERBOOK_PERMISSION_CONFIRMED=0 until you set it yourself."
  local ans want=0
  while true; do
    read -r -p "Do you want to set up the RepeaterBook mirror? [yes/no]: " ans
    case "${ans,,}" in
      y|yes) want=1; break ;;
      n|no) want=0; break ;;
      *) echo "Please answer yes or no." ;;
    esac
  done
  if [[ "$want" -eq 0 ]]; then
    log "RepeaterBook mirror skipped (left disabled)"
    return 0
  fi

  local app_name app_ver contact countries token token2
  read -r -p "App name (must match RepeaterBook approval) [navi-server-repeaterbook]: " app_name
  app_name="${app_name:-navi-server-repeaterbook}"
  read -r -p "App version [0.1]: " app_ver
  app_ver="${app_ver:-0.1}"
  read -r -p "Contact email (must match approval): " contact
  [[ -n "$contact" ]] || fail "contact email required"
  read -r -p "Countries (comma-separated RepeaterBook names) [Norway]: " countries
  countries="${countries:-Norway}"

  set +o history
  read -r -s -p "Server/backend app_ token: " token
  echo
  read -r -s -p "Confirm app_ token: " token2
  echo
  set -o history
  [[ "$token" == "$token2" ]] || fail "token mismatch"
  [[ "$token" == app_* ]] || fail "token must start with app_ (server/backend token only; not rbuapp_)"
  [[ "$token" != rbuapp_* ]] || fail "rbuapp_ tokens are not allowed"

  mkdir -p "${DATA}/secrets" "${DATA}/repeaterbook/staging" "${DATA}/published/repeaters"
  printf '%s' "$token" | python3 -c '
import pathlib, sys
path = pathlib.Path(sys.argv[1])
token = sys.stdin.read()
path.write_text(
    "# Written by setup-server.sh RepeaterBook setup — mode 0600. Do not commit.\n"
    f"REPEATERBOOK_APP_TOKEN={token}\n",
    encoding="utf-8",
)
' "$RB_SECRETS"
  chown "${SERVICE_USER}:${SERVICE_USER}" "$RB_SECRETS"
  chmod 600 "$RB_SECRETS"

  _rb_set_config() {
    local key="$1" val="$2" cfg="${DATA}/config.env" line
    touch "$cfg"
    # %q so values with spaces (country lists) source cleanly.
    printf -v line '%s=%q' "$key" "$val"
    if grep -q "^${key}=" "$cfg" 2>/dev/null; then
      grep -v "^${key}=" "$cfg" >"${cfg}.tmp"
      mv "${cfg}.tmp" "$cfg"
    fi
    printf '%s\n' "$line" >>"$cfg"
  }
  _rb_set_config NAVI_REPEATERBOOK_ENABLED 1
  _rb_set_config REPEATERBOOK_PERMISSION_CONFIRMED 0
  _rb_set_config NAVI_REPEATERBOOK_BASE_URL "https://www.repeaterbook.com/api"
  _rb_set_config NAVI_REPEATERBOOK_COUNTRIES "$countries"
  _rb_set_config NAVI_REPEATERBOOK_APP_NAME "$app_name"
  _rb_set_config NAVI_REPEATERBOOK_APP_VERSION "$app_ver"
  _rb_set_config NAVI_REPEATERBOOK_CONTACT "$contact"
  _rb_set_config NAVI_REPEATERBOOK_SECRETS_FILE "$RB_SECRETS"

  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${DATA}/secrets" "${DATA}/repeaterbook" \
    "${DATA}/published/repeaters"
  chmod 700 "${DATA}/secrets" "${DATA}/repeaterbook"

  install -m 0644 "$RB_UNIT_SRC" "${SYSTEMD_DIR}/navi-repeaterbook.service"
  install -m 0644 "$RB_TIMER_SRC" "${SYSTEMD_DIR}/navi-repeaterbook.timer"
  systemctl daemon-reload
  systemctl enable --now navi-repeaterbook.timer
  log "installed navi-repeaterbook.timer (Wed 03:30 UTC)"
  log "REPEATERBOOK_PERMISSION_CONFIRMED remains 0 — set to 1 in ${DATA}/config.env only after written permission"
  log "manual run (after permission flag): sudo -u ${SERVICE_USER} ${NAVI_SERVER_ROOT}/scripts/repeaterbook-mirror.sh"
  log "uninstall: sudo ${NAVI_SERVER_ROOT}/scripts/uninstall-repeaterbook.sh [--purge]"
}


# Interactive RadioID mirror enable. Does NOT set RADIOID_PERMISSION_CONFIRMED.
radioid_apply() {
  require_root "RadioID setup"
  command -v python3 >/dev/null 2>&1 || fail "python3 not found"
  command -v zstd >/dev/null 2>&1 || fail "zstd not found (apt install zstd)"
  [[ -f "$RB_UNIT_SRC" ]] || fail "missing ${RB_UNIT_SRC}"
  [[ -f "$RB_TIMER_SRC" ]] || fail "missing ${RB_TIMER_SRC}"
  [[ -x "${NAVI_SERVER_ROOT}/scripts/radioid-mirror.sh" ]] \
    || fail "missing executable ${NAVI_SERVER_ROOT}/scripts/radioid-mirror.sh"
  [[ -x "${NAVI_SERVER_ROOT}/scripts/repeater-mirror-all.sh" ]] \
    || fail "missing executable ${NAVI_SERVER_ROOT}/scripts/repeater-mirror-all.sh"
  [[ -t 0 ]] || fail "RadioID setup requires an interactive TTY"

  echo "=== RadioID mirror (optional) ==="
  echo "Requires written permission from RadioID before any fetch."
  echo "Optional X-API-Token may be stored; anonymous works if no token given."
  echo "RADIOID_PERMISSION_CONFIRMED stays 0 until you set it yourself."
  local ans want=0
  while true; do
    read -r -p "Do you want to set up the RadioID mirror? [yes/no]: " ans
    case "${ans,,}" in
      y|yes) want=1; break ;;
      n|no) want=0; break ;;
      *) echo "Please answer yes or no." ;;
    esac
  done
  if [[ "$want" -eq 0 ]]; then
    log "RadioID mirror skipped (left disabled)"
    return 0
  fi

  local app_name app_ver contact countries token token2
  read -r -p "App name for User-Agent [navi-server-radioid]: " app_name
  app_name="${app_name:-navi-server-radioid}"
  read -r -p "App version [0.1]: " app_ver
  app_ver="${app_ver:-0.1}"
  read -r -p "Contact email: " contact
  [[ -n "$contact" ]] || fail "contact email required"
  read -r -p "Countries (comma-separated) [Norway]: " countries
  countries="${countries:-Norway}"

  set +o history
  read -r -s -p "Optional RadioID X-API-Token (Enter to skip): " token
  echo
  set -o history

  mkdir -p "${DATA}/secrets" "${DATA}/radioid/cache" "${DATA}/radioid/state" \
    "${DATA}/published/repeaters"
  if [[ -n "${token}" ]]; then
    printf '%s' "$token" | python3 -c '
import pathlib, sys
path = pathlib.Path(sys.argv[1])
token = sys.stdin.read().strip()
path.write_text(
    "# Written by setup-server.sh RadioID setup — mode 0600. Do not commit.\\n"
    f"RADIOID_API_TOKEN={token}\\n",
    encoding="utf-8",
)
' "$RID_SECRETS"
    chown "${SERVICE_USER}:${SERVICE_USER}" "$RID_SECRETS"
    chmod 600 "$RID_SECRETS"
    log "wrote optional RadioID secrets file"
  else
    log "no RadioID token provided — will run anonymously when permission confirmed"
  fi

  _rid_set_config() {
    local key="$1" val="$2" cfg="${DATA}/config.env" line
    touch "$cfg"
    # %q so values with spaces (country lists) source cleanly.
    printf -v line '%s=%q' "$key" "$val"
    if grep -q "^${key}=" "$cfg" 2>/dev/null; then
      grep -v "^${key}=" "$cfg" >"${cfg}.tmp"
      mv "${cfg}.tmp" "$cfg"
    fi
    printf '%s\n' "$line" >>"$cfg"
  }
  _rid_set_config NAVI_RADIOID_ENABLED 1
  _rid_set_config RADIOID_PERMISSION_CONFIRMED 0
  _rid_set_config NAVI_RADIOID_COUNTRIES "$countries"
  _rid_set_config NAVI_RADIOID_APP_NAME "$app_name"
  _rid_set_config NAVI_RADIOID_APP_VERSION "$app_ver"
  _rid_set_config NAVI_RADIOID_CONTACT "$contact"
  _rid_set_config NAVI_RADIOID_SECRETS_FILE "$RID_SECRETS"

  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${DATA}/secrets" "${DATA}/radioid" \
    "${DATA}/published/repeaters" 2>/dev/null || true
  chmod 700 "${DATA}/secrets" "${DATA}/radioid"

  install -m 0644 "$RB_UNIT_SRC" "${SYSTEMD_DIR}/navi-repeaterbook.service"
  install -m 0644 "$RB_TIMER_SRC" "${SYSTEMD_DIR}/navi-repeaterbook.timer"
  systemctl daemon-reload
  systemctl enable --now navi-repeaterbook.timer
  log "installed/enabled navi-repeaterbook.timer (runs both sources; each gates itself)"
  log "RADIOID_PERMISSION_CONFIRMED remains 0 — set to 1 only after written permission"
  log "uninstall: sudo ${NAVI_SERVER_ROOT}/scripts/uninstall-radioid.sh [--purge]"
}

# Non-interactive OSM repeaters enable (ODbL — no permission flag / secrets).
osm_repeaters_apply() {
  require_root "OSM repeaters setup"
  command -v python3 >/dev/null 2>&1 || fail "python3 not found"
  command -v zstd >/dev/null 2>&1 || fail "zstd not found (apt install zstd)"
  command -v osmium >/dev/null 2>&1 || fail "osmium not found (apt install osmium-tool)"
  [[ -x "${NAVI_SERVER_ROOT}/scripts/osm-repeaters.sh" ]] \
    || fail "missing executable ${NAVI_SERVER_ROOT}/scripts/osm-repeaters.sh"

  mkdir -p "${DATA}/osm_repeaters/staging" "${DATA}/published/repeaters"
  touch "${DATA}/config.env"
  if grep -q '^NAVI_OSM_REPEATERS_ENABLED=' "${DATA}/config.env" 2>/dev/null; then
    sed -i 's/^NAVI_OSM_REPEATERS_ENABLED=.*/NAVI_OSM_REPEATERS_ENABLED=1/' "${DATA}/config.env"
  else
    printf '%s\n' 'NAVI_OSM_REPEATERS_ENABLED=1' >>"${DATA}/config.env"
  fi
  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${DATA}/osm_repeaters" \
    "${DATA}/published/repeaters" 2>/dev/null || true
  chmod 700 "${DATA}/osm_repeaters"
  log "NAVI_OSM_REPEATERS_ENABLED=1 (runs from weekly bake after publish; soft-fail)"
  log "manual run: sudo -u ${SERVICE_USER} ${NAVI_SERVER_ROOT}/scripts/osm-repeaters.sh"
  log "uninstall: sudo ${NAVI_SERVER_ROOT}/scripts/uninstall-osm-repeaters.sh [--purge]"
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
  if [[ -d "${DATA}/published/mwm" ]]; then
    echo "OK published/mwm/"
    [[ -f "${DATA}/published/mwm/.navi-mwm-owned" ]] && echo "OK .navi-mwm-owned" || echo "MISSING .navi-mwm-owned"
  else
    echo "MISSING published/mwm/"
  fi
  if [[ -f "${DATA}/published/.navi-rkyv-frozen" ]]; then
    echo "OK .navi-rkyv-frozen (serving-only .rkyv)"
  else
    echo "NOTE .navi-rkyv-frozen absent (weekly .rkyv bake may be intended)"
  fi
  if [[ -x "$MWM_SIDECAR_BIN" ]]; then
    echo "OK navi-mwm-sidecar (${MWM_SIDECAR_BIN})"
  else
    echo "MISSING navi-mwm-sidecar (cargo build --release -p navi-mwm-sidecar)"
  fi
  if [[ -x "${MWM_VENV}/bin/python" ]]; then
    echo "OK MWM venv ${MWM_VENV}"
  else
    echo "MISSING MWM venv (re-run setup-server.sh)"
  fi
  if [[ -f "${SYSTEMD_DIR}/navi-mwm-check.timer" ]]; then
    echo "OK mwm check timer installed (enabled=$(systemctl is-enabled navi-mwm-check.timer 2>/dev/null || echo no))"
  else
    echo "MISSING navi-mwm-check.timer (sudo $0 --apply-mwm)"
  fi
  if [[ -f "${SYSTEMD_DIR}/navi-pack-scrub.service" && -f "${SYSTEMD_DIR}/navi-pack-scrub.timer" ]]; then
    echo "OK scrub units installed"
    if systemctl is-enabled navi-pack-scrub.timer >/dev/null 2>&1; then
      echo "OK navi-pack-scrub.timer enabled ($(systemctl is-enabled navi-pack-scrub.timer))"
    else
      echo "OK navi-pack-scrub.timer disabled (expected when .rkyv frozen)"
    fi
  else
    echo "MISSING scrub units (sudo $0 --apply-service)"
  fi
  if [[ -f "${SYSTEMD_DIR}/navi-pack-bake.service" && -f "${SYSTEMD_DIR}/navi-pack-bake.timer" ]]; then
    echo "OK pack bake units installed"
    if systemctl is-enabled navi-pack-bake.timer >/dev/null 2>&1; then
      echo "OK navi-pack-bake.timer enabled ($(systemctl is-enabled navi-pack-bake.timer))"
    else
      echo "OK navi-pack-bake.timer disabled (expected when .rkyv frozen)"
    fi
  else
    echo "MISSING pack bake units (sudo $0 --apply-service)"
  fi
  code_mwm="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/mwm/current.json || true)"
  echo "GET /mwm/current.json -> ${code_mwm}"
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
  if [[ -f "${SYSTEMD_DIR}/navi-repeaterbook.timer" ]]; then
    echo "OK repeaterbook units installed (enabled=$(systemctl is-enabled navi-repeaterbook.timer 2>/dev/null || echo no))"
  else
    echo "RepeaterBook units not installed (optional/off-by-default: sudo $0 --apply-repeaterbook)"
  fi
  if [[ -f "${DATA}/config.env" ]] && grep -q '^NAVI_REPEATERBOOK_ENABLED=1' "${DATA}/config.env" 2>/dev/null; then
    echo "OK RepeaterBook feature flag ENABLED in config.env"
  else
    echo "OK RepeaterBook feature flag off/absent (inert default)"
  fi
  if [[ -f "${DATA}/config.env" ]] && grep -q '^REPEATERBOOK_PERMISSION_CONFIRMED=1' "${DATA}/config.env" 2>/dev/null; then
    echo "OK REPEATERBOOK_PERMISSION_CONFIRMED=1"
  else
    echo "OK REPEATERBOOK_PERMISSION_CONFIRMED off/absent (no upstream fetch)"
  fi
  if [[ -f "${DATA}/config.env" ]] && grep -q '^NAVI_OSM_REPEATERS_ENABLED=1' "${DATA}/config.env" 2>/dev/null; then
    echo "OK OSM repeaters feature flag ENABLED in config.env"
  else
    echo "OK OSM repeaters feature flag off/absent (inert default)"
  fi
  if command -v osmium >/dev/null 2>&1; then
    echo "OK osmium ($(osmium --version 2>/dev/null | head -1))"
  else
    echo "MISSING osmium (needed for --apply-osm-repeaters / weekly extract)"
  fi
  if command -v apache2ctl >/dev/null 2>&1; then
    apache2ctl -S 2>&1 | grep -E 'navi-packs|\*:80' || true
  fi
  if [[ -f "$DATEX_REWRITE_DST" ]]; then
    echo "OK DATEX rewrite snippet ${DATEX_REWRITE_DST}"
  else
    echo "MISSING DATEX rewrite snippet (sudo $0 --apply-apache)"
  fi
  if [[ -f "$NAVI_PACKS_COMMON_CONF" ]] && grep -q 'apache-navi-datex-rewrites.conf' "$NAVI_PACKS_COMMON_CONF"; then
    echo "OK HTTPS common conf includes DATEX rewrites"
  elif [[ -f "$NAVI_PACKS_COMMON_CONF" ]]; then
    echo "MISSING DATEX Include in ${NAVI_PACKS_COMMON_CONF} (sudo $0 --apply-apache)"
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
ensure_mwm_dirs
ensure_mwm_venv || true

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


ensure_datex_rewrite_include() {
  local conf="${NAVI_PACKS_COMMON_CONF}"
  local inc="Include ${DATEX_REWRITE_DST}"
  if [[ ! -f "$conf" ]]; then
    log "WARN: ${conf} missing — :443 DATEX redirects not installed"
    return 0
  fi
  python3 - "$conf" "$inc" <<'PY2'
import sys
from pathlib import Path
path = Path(sys.argv[1])
inc = sys.argv[2].rstrip() + "\n"
text = path.read_text(encoding="utf-8")
if inc.strip() in text:
    raise SystemExit(0)
engine = "RewriteEngine On\n"
inserted = False
for needle in ("RewriteRule ^ - [R=403,L]\n", "RewriteRule ^ - [R=405,L]\n"):
    if needle in text:
        text = text.replace(needle, needle + inc, 1)
        inserted = True
        break
if not inserted:
    if engine in text:
        text = text.replace(engine, engine + inc, 1)
    else:
        text = engine + inc + text
path.write_text(text, encoding="utf-8")
PY2
}

# --- Apache (sudo) ---
apache_apply() {
  [[ -f "$APACHE_SRC" ]] || fail "missing ${APACHE_SRC}"
  [[ -f "$DATEX_REWRITE_SRC" ]] || fail "missing ${DATEX_REWRITE_SRC}"
  command -v apache2ctl >/dev/null 2>&1 || fail "apache2 not installed"
  datex_migrate_publish_layout
  cp "$DATEX_REWRITE_SRC" "$DATEX_REWRITE_DST"
  ensure_datex_rewrite_include
  cp "$APACHE_SRC" "$APACHE_DST"
  a2dissite 000-default.conf >/dev/null 2>&1 || true
  a2ensite apache-navi-packs >/dev/null
  apache2ctl configtest
  systemctl reload apache2
  log "Apache site apache-navi-packs enabled on :80"
}

if [[ "$APPLY_SERVICE" -eq 1 ]]; then
  navit_server_apply
elif [[ "$APPLY_MWM" -eq 1 ]]; then
  mwm_units_apply
fi

if [[ "$APPLY_DDNS" -eq 1 ]]; then
  ddns_apply
fi

if [[ "$APPLY_DATEX" -eq 1 ]]; then
  datex_apply
fi

if [[ "$APPLY_REPEATERBOOK" -eq 1 ]]; then
  repeaterbook_apply
fi

if [[ "$APPLY_RADIOID" -eq 1 ]]; then
  radioid_apply
fi

if [[ "$APPLY_OSM_REPEATERS" -eq 1 ]]; then
  osm_repeaters_apply
fi

if [[ "$APPLY_APACHE" -eq 1 ]]; then
  require_root "--apply-apache"
  apache_apply
elif [[ "$(id -u)" -eq 0 && "$APPLY_SERVICE" -eq 0 && "$APPLY_MWM" -eq 0 && "$APPLY_DDNS" -eq 0 && "$APPLY_DATEX" -eq 0 && "$APPLY_REPEATERBOOK" -eq 0 && "$APPLY_RADIOID" -eq 0 && "$APPLY_OSM_REPEATERS" -eq 0 ]]; then
  # Full sudo run without flags: configure Apache + service user, then offer DATEX.
  apache_apply
  navit_server_apply
  if [[ -t 0 ]]; then
    datex_apply
  else
    log "DATEX left disabled (non-interactive; re-run with --apply-datex on a TTY to enable)"
  fi
elif [[ "$(id -u)" -ne 0 && "$APPLY_SERVICE" -eq 0 && "$APPLY_MWM" -eq 0 && "$APPLY_APACHE" -eq 0 && "$APPLY_DDNS" -eq 0 && "$APPLY_DATEX" -eq 0 && "$APPLY_REPEATERBOOK" -eq 0 && "$APPLY_RADIOID" -eq 0 && "$APPLY_OSM_REPEATERS" -eq 0 ]]; then
  cat <<EOF

Apache / dedicated service user / optional plugins are not configured by this non-root run. Apply with:

  sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --apply-apache
  sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --apply-service
  # MWM units only (also included in --apply-service):
  sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --apply-mwm
  # Opt-in weekly .rkyv bake (default is frozen/disabled):
  sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --apply-service --enable-rkyv-bake
  # Optional Dynamic DNS (edit data/ddns.env first):
  sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --apply-ddns
  # Optional DATEX / RepeaterBook / RadioID / OSM repeaters: see --help

Or full sudo setup (Apache + service + DATEX yes/no prompt):

  sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh

Migrate MWM data only (no .rkyv): scripts/migrate-mwm.sh — see docs/mwm-pipeline.md

EOF
fi

# --- Rust binaries (pack convert + MWM sidecar) ---
cargo_bin=""
if command -v cargo >/dev/null 2>&1; then
  cargo_bin="$(command -v cargo)"
elif [[ -x "${HOME}/.cargo/bin/cargo" ]]; then
  cargo_bin="${HOME}/.cargo/bin/cargo"
fi
if [[ -n "$cargo_bin" ]]; then
  if [[ ! -x "$CONVERT_BIN" ]]; then
    log "building navi-indexed-convert (release)"
    (cd "$NAVI_SERVER_ROOT" && CARGO_TARGET_DIR="${NAVI_SERVER_ROOT}/target" \
      "$cargo_bin" build --release -p navi-indexed-convert)
  fi
  if [[ ! -x "$MWM_SIDECAR_BIN" ]]; then
    log "building navi-mwm-sidecar (release)"
    (cd "$NAVI_SERVER_ROOT" && CARGO_TARGET_DIR="${NAVI_SERVER_ROOT}/target" \
      "$cargo_bin" build --release -p navi-mwm-sidecar) || \
      warn "navi-mwm-sidecar build failed"
  fi
else
  warn "cargo not found — build later: cargo build --release -p navi-indexed-convert -p navi-mwm-sidecar"
fi
[[ -x "$CONVERT_BIN" ]] && log "convert binary present: ${CONVERT_BIN}" || warn "convert binary missing"
[[ -x "$MWM_SIDECAR_BIN" ]] && log "mwm sidecar present: ${MWM_SIDECAR_BIN}" || warn "navi-mwm-sidecar missing"
if [[ -x "${NAVI_MWM_BUILD_DIR:-/var/lib/navi/mwm/omim-build-release}/routes_builder_tool" ]]; then
  log "routes_builder_tool present (optional validation)"
else
  log "routes_builder_tool absent (optional; build via scripts/mwm/build-generator.sh when validating routes)"
fi

cat <<EOF

== setup summary ==
root:     ${NAVI_SERVER_ROOT}
published/mwm + packs dirs: ensured
.rkyv frozen default: RKYV_FROZEN=${RKYV_FROZEN} (bake/scrub disabled unless --enable-rkyv-bake)
MWM:      NAVI_MWM_ENABLED=0 in ${MWM_ENV_DST} (when applied); check timer detect-only
venv:     ${MWM_VENV}
migrate:  ${NAVI_SERVER_ROOT}/scripts/migrate-mwm.sh --source … --dest …   # dry-run
          ${NAVI_SERVER_ROOT}/scripts/migrate-mwm.sh --source … --dest … --apply
verify:   ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --check
docs:     docs/mwm-pipeline.md

EOF
log "setup complete"
