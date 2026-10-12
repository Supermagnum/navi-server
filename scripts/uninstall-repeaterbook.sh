#!/usr/bin/env bash
# Remove RepeaterBook mirror units and optionally secrets/published data.
#
# Usage:
#   sudo /media/navi/navi-server/scripts/uninstall-repeaterbook.sh
#   sudo /media/navi/navi-server/scripts/uninstall-repeaterbook.sh --purge
#   /media/navi/navi-server/scripts/uninstall-repeaterbook.sh --check

set -euo pipefail

NAVI_SERVER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${NAVI_SERVER_ROOT}/data"
SYSTEMD_DIR="/etc/systemd/system"
UNITS=(navi-repeaterbook.timer navi-repeaterbook.service)
SECRETS="${DATA}/secrets/repeaterbook.env"
PUBLISHED="${DATA}/published/repeaters"

PURGE=0
CHECK_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge) PURGE=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help)
      sed -n '2,10p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "INFO" "$*"; }
fail() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "FAILED" "$*"; exit 1; }

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "== navi-repeaterbook uninstall check =="
  for u in "${UNITS[@]}"; do
    if [[ -f "${SYSTEMD_DIR}/${u}" ]]; then
      echo "INSTALLED ${u} enabled=$(systemctl is-enabled "$u" 2>/dev/null || echo no)"
    else
      echo "ABSENT ${u}"
    fi
  done
  [[ -f "$SECRETS" ]] && echo "PRESENT secrets" || echo "ABSENT secrets"
  [[ -d "$PUBLISHED" ]] && echo "PRESENT published/repeaters" || echo "ABSENT published/repeaters"
  [[ -d "${DATA}/repeaterbook" ]] && echo "PRESENT data/repeaterbook" || echo "ABSENT data/repeaterbook"
  exit 0
fi

[[ "$(id -u)" -eq 0 ]] || fail "uninstall-repeaterbook.sh requires root (re-run with sudo)"
command -v systemctl >/dev/null 2>&1 || fail "systemctl not found"

systemctl disable --now navi-repeaterbook.timer 2>/dev/null || true
systemctl stop navi-repeaterbook.service 2>/dev/null || true

for u in "${UNITS[@]}"; do
  if [[ -f "${SYSTEMD_DIR}/${u}" ]]; then
    rm -f "${SYSTEMD_DIR}/${u}"
    log "removed ${SYSTEMD_DIR}/${u}"
  else
    log "already absent ${u}"
  fi
done
systemctl daemon-reload
systemctl reset-failed navi-repeaterbook.service navi-repeaterbook.timer 2>/dev/null || true

# Clear enable flags in config.env (leave other keys).
CFG="${DATA}/config.env"
if [[ -f "$CFG" ]]; then
  sed -i \
    -e 's/^NAVI_REPEATERBOOK_ENABLED=.*/NAVI_REPEATERBOOK_ENABLED=0/' \
    -e 's/^REPEATERBOOK_PERMISSION_CONFIRMED=.*/REPEATERBOOK_PERMISSION_CONFIRMED=0/' \
    "$CFG" || true
  log "set NAVI_REPEATERBOOK_ENABLED=0 and REPEATERBOOK_PERMISSION_CONFIRMED=0 in config.env"
fi

rm -rf "${DATA}/published/repeaters"
log "removed published/repeaters"

if [[ "$PURGE" -eq 1 ]]; then
  rm -f "$SECRETS"
  rm -rf "${DATA}/repeaterbook"
  log "purged secrets and data/repeaterbook"
else
  log "secrets retained (pass --purge to delete ${SECRETS})"
fi

log "repeaterbook uninstall complete"
