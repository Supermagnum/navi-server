#!/usr/bin/env bash
# Remove Dynamic DNS units and (optionally) local DDNS config/state installed
# by setup-server.sh --apply-ddns.
#
# Usage:
#   sudo /media/navi/navi-server/scripts/uninstall-ddns.sh
#   sudo /media/navi/navi-server/scripts/uninstall-ddns.sh --purge
#   /media/navi/navi-server/scripts/uninstall-ddns.sh --check

set -euo pipefail

NAVI_SERVER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${NAVI_SERVER_ROOT}/data"
SYSTEMD_DIR="/etc/systemd/system"
UNITS=(navi-ddns.timer navi-ddns.service)

PURGE=0
CHECK_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge) PURGE=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "INFO" "$*"; }
fail() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "FAILED" "$*"; exit 1; }

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "== navi-ddns uninstall check =="
  for u in "${UNITS[@]}"; do
    if [[ -f "${SYSTEMD_DIR}/${u}" ]]; then
      echo "INSTALLED ${u} enabled=$(systemctl is-enabled "$u" 2>/dev/null || echo no) active=$(systemctl is-active "$u" 2>/dev/null || echo n/a)"
    else
      echo "ABSENT ${u}"
    fi
  done
  [[ -f "${DATA}/ddns.env" ]] && echo "PRESENT ${DATA}/ddns.env" || echo "ABSENT ${DATA}/ddns.env"
  [[ -f "${DATA}/state/ddns.last_ipv4" ]] && echo "PRESENT ${DATA}/state/ddns.last_ipv4" || echo "ABSENT state"
  exit 0
fi

[[ "$(id -u)" -eq 0 ]] || fail "uninstall-ddns.sh requires root (re-run with sudo)"

command -v systemctl >/dev/null 2>&1 || fail "systemctl not found"

if systemctl list-unit-files navi-ddns.timer >/dev/null 2>&1; then
  systemctl disable --now navi-ddns.timer 2>/dev/null || true
fi
systemctl stop navi-ddns.service 2>/dev/null || true

for u in "${UNITS[@]}"; do
  if [[ -f "${SYSTEMD_DIR}/${u}" ]]; then
    rm -f "${SYSTEMD_DIR}/${u}"
    log "removed ${SYSTEMD_DIR}/${u}"
  else
    log "already absent ${u}"
  fi
done

systemctl daemon-reload
systemctl reset-failed navi-ddns.service navi-ddns.timer 2>/dev/null || true

if [[ "$PURGE" -eq 1 ]]; then
  if [[ -f "${DATA}/ddns.env" ]]; then
    rm -f "${DATA}/ddns.env"
    log "purged ${DATA}/ddns.env"
  fi
  if [[ -f "${DATA}/state/ddns.last_ipv4" ]]; then
    rm -f "${DATA}/state/ddns.last_ipv4" "${DATA}/state/ddns.last_ipv4.partial"
    log "purged ddns state"
  fi
else
  log "kept ${DATA}/ddns.env and state (pass --purge to delete credentials/state)"
fi

log "Dynamic DNS uninstalled"
log "re-install later: edit data/ddns.env then sudo ${NAVI_SERVER_ROOT}/scripts/setup-server.sh --apply-ddns"
