#!/usr/bin/env bash
# Remove DATEX NPRA redistribution units, cron leftovers, and optionally
# secrets/cache/published snapshots installed by setup-server.sh --apply-datex.
#
# Removes only published/datex/npra/ (plus legacy flat NPRA filenames). Does not
# wipe the whole published/datex/ tree when other providers may exist.
#
# Usage:
#   sudo /media/navi/navi-server/scripts/uninstall-datex-npra.sh
#   sudo /media/navi/navi-server/scripts/uninstall-datex-npra.sh --purge
#   /media/navi/navi-server/scripts/uninstall-datex-npra.sh --check

set -euo pipefail

NAVI_SERVER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${NAVI_SERVER_ROOT}/data"
SYSTEMD_DIR="/etc/systemd/system"
UNITS=(navi-datex-npra.timer navi-datex-npra.service)
SECRETS="${DATA}/secrets/datex_npra.env"
NPRA_PUBLISHED="${DATA}/published/datex/npra"

PURGE=0
CHECK_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge) PURGE=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "INFO" "$*"; }
fail() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "FAILED" "$*"; exit 1; }

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "== navi-datex-npra uninstall check =="
  for u in "${UNITS[@]}"; do
    if [[ -f "${SYSTEMD_DIR}/${u}" ]]; then
      echo "INSTALLED ${u} enabled=$(systemctl is-enabled "$u" 2>/dev/null || echo no)"
    else
      echo "ABSENT ${u}"
    fi
  done
  [[ -f "$SECRETS" ]] && echo "PRESENT secrets" || echo "ABSENT secrets"
  [[ -d "$NPRA_PUBLISHED" ]] && echo "PRESENT published/datex/npra" || echo "ABSENT published/datex/npra"
  [[ -d "${DATA}/published/datex" ]] && echo "PRESENT published/datex" || echo "ABSENT published/datex"
  [[ -d "${DATA}/datex_npra" ]] && echo "PRESENT data/datex_npra" || echo "ABSENT data/datex_npra"
  exit 0
fi

[[ "$(id -u)" -eq 0 ]] || fail "uninstall-datex-npra.sh requires root (re-run with sudo)"
command -v systemctl >/dev/null 2>&1 || fail "systemctl not found"

systemctl disable --now navi-datex-npra.timer 2>/dev/null || true
systemctl stop navi-datex-npra.service 2>/dev/null || true

for u in "${UNITS[@]}"; do
  if [[ -f "${SYSTEMD_DIR}/${u}" ]]; then
    rm -f "${SYSTEMD_DIR}/${u}"
    log "removed ${SYSTEMD_DIR}/${u}"
  else
    log "already absent ${u}"
  fi
done
systemctl daemon-reload
systemctl reset-failed navi-datex-npra.service navi-datex-npra.timer 2>/dev/null || true

# Remove any legacy cron entries (this feature uses systemd timers only;
# clean cron so a mistaken hand-install cannot keep polling).
remove_cron_crumbs() {
  local f
  for f in \
    /etc/cron.d/navi-datex-npra \
    /etc/cron.d/navi-datex \
    /etc/cron.daily/navi-datex-npra \
    /etc/cron.hourly/navi-datex-npra; do
    if [[ -e "$f" ]]; then
      rm -f "$f"
      log "removed cron file ${f}"
    fi
  done
  if command -v crontab >/dev/null 2>&1; then
    if crontab -l 2>/dev/null | grep -qE 'datex-npra|navi-datex-npra'; then
      crontab -l 2>/dev/null | grep -vE 'datex-npra|navi-datex-npra' | crontab - || true
      log "stripped datex lines from root crontab"
    fi
    if id -u navit-server >/dev/null 2>&1; then
      if crontab -u navit-server -l 2>/dev/null | grep -qE 'datex-npra|navi-datex-npra'; then
        crontab -u navit-server -l 2>/dev/null | grep -vE 'datex-npra|navi-datex-npra' \
          | crontab -u navit-server - || true
        log "stripped datex lines from navit-server crontab"
      fi
    fi
  fi
}
remove_cron_crumbs

# Disable feature flag in config.env when present (keeps rest of file).
if [[ -f "${DATA}/config.env" ]] && grep -q '^NAVI_DATEX_NPRA_ENABLED=' "${DATA}/config.env"; then
  sed -i 's/^NAVI_DATEX_NPRA_ENABLED=.*/NAVI_DATEX_NPRA_ENABLED=0/' "${DATA}/config.env"
  log "set NAVI_DATEX_NPRA_ENABLED=0 in config.env"
fi

# Remove NPRA published tree only; leave other providers under published/datex/.
if [[ -d "$NPRA_PUBLISHED" ]]; then
  rm -rf "$NPRA_PUBLISHED"
  log "removed ${NPRA_PUBLISHED}"
fi
# Legacy flat NPRA filenames (pre-namespaced layout).
flat="${DATA}/published/datex"
if [[ -d "$flat" ]]; then
  for f in GetSituation.xml GetTravelTimeData.xml GetMeasuredWeatherData.xml \
           GetCCTVSiteTable.xml source.json; do
    if [[ -f "${flat}/${f}" ]]; then
      rm -f "${flat}/${f}"
      log "removed legacy flat ${flat}/${f}"
    fi
  done
  if [[ -d "${NAVI_SERVER_ROOT}/plugins/datex_common" ]]; then
    PYTHONPATH="${NAVI_SERVER_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
      NAVI_PACK_ROOT="${DATA}" \
      python3 -c '
import os
from pathlib import Path
from plugins.datex_common.providers_index import rebuild_providers_index
rebuild_providers_index(Path(os.environ["NAVI_PACK_ROOT"]))
' >/dev/null && log "refreshed ${flat}/providers.json" || true
  fi
fi

if [[ "$PURGE" -eq 1 ]]; then
  rm -f "$SECRETS"
  rm -rf "${DATA}/datex_npra"
  # Only remove secrets dir if empty / only held NPRA secrets.
  if [[ -d "${DATA}/secrets" ]] && [[ -z "$(ls -A "${DATA}/secrets" 2>/dev/null || true)" ]]; then
    rmdir "${DATA}/secrets" 2>/dev/null || true
  fi
  log "purged NPRA secrets and data/datex_npra"
else
  log "kept secrets/cache (pass --purge to delete credentials and state)"
fi

log "DATEX NPRA redistribution uninstalled"
log "re-enable later: sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --apply-datex"
