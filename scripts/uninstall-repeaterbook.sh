#!/usr/bin/env bash
# Remove RepeaterBook mirror units/flags and published/repeaters/repeaterbook/.
# Does not remove RadioID data (use uninstall-radioid.sh).
#
# Usage:
#   sudo …/uninstall-repeaterbook.sh
#   sudo …/uninstall-repeaterbook.sh --purge
#   …/uninstall-repeaterbook.sh --check

set -euo pipefail
NAVI_SERVER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${NAVI_SERVER_ROOT}/data"
SYSTEMD_DIR="/etc/systemd/system"
UNITS=(navi-repeaterbook.timer navi-repeaterbook.service)
SECRETS="${DATA}/secrets/repeaterbook.env"
PUB_SRC="${DATA}/published/repeaters/repeaterbook"

PURGE=0
CHECK_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge) PURGE=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "INFO" "$*"; }
fail() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "FAILED" "$*"; exit 1; }

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "== uninstall-repeaterbook check =="
  for u in "${UNITS[@]}"; do
    [[ -f "${SYSTEMD_DIR}/${u}" ]] && echo "INSTALLED $u" || echo "ABSENT $u"
  done
  [[ -f "$SECRETS" ]] && echo "PRESENT secrets" || echo "ABSENT secrets"
  [[ -d "$PUB_SRC" ]] && echo "PRESENT $PUB_SRC" || echo "ABSENT published repeaterbook"
  exit 0
fi

[[ "$(id -u)" -eq 0 ]] || fail "requires root"
# Only remove shared timer if RadioID is also disabled / absent.
rid_on=0
grep -q '^NAVI_RADIOID_ENABLED=1' "${DATA}/config.env" 2>/dev/null && rid_on=1 || true
[[ -d "${DATA}/published/repeaters/radioid" ]] && rid_on=1 || true

if [[ "$rid_on" -eq 0 ]]; then
  systemctl disable --now navi-repeaterbook.timer 2>/dev/null || true
  systemctl stop navi-repeaterbook.service 2>/dev/null || true
  for u in "${UNITS[@]}"; do
    rm -f "${SYSTEMD_DIR}/${u}"
  done
  systemctl daemon-reload 2>/dev/null || true
  log "removed shared navi-repeaterbook timer/service (RadioID not active)"
else
  log "kept navi-repeaterbook timer (RadioID still present/enabled)"
fi

CFG="${DATA}/config.env"
if [[ -f "$CFG" ]]; then
  sed -i \
    -e 's/^NAVI_REPEATERBOOK_ENABLED=.*/NAVI_REPEATERBOOK_ENABLED=0/' \
    -e 's/^REPEATERBOOK_PERMISSION_CONFIRMED=.*/REPEATERBOOK_PERMISSION_CONFIRMED=0/' \
    "$CFG" || true
fi
rm -rf "$PUB_SRC"
# Drop repeaterbook entry from current.json
if [[ -f "${DATA}/published/repeaters/current.json" ]]; then
  CUR="${DATA}/published/repeaters/current.json" \
  PYTHONPATH="${NAVI_SERVER_ROOT}${PYTHONPATH:+:${PYTHONPATH}}" python3 - <<'PY' || true
import json, os
from pathlib import Path
p=Path(os.environ["CUR"])
doc=json.loads(p.read_text())
doc["sources"]=[s for s in doc.get("sources") or [] if s.get("source")!="repeaterbook"]
p.write_text(json.dumps(doc, indent=2, sort_keys=True)+"\n")
PY
fi
if [[ "$PURGE" -eq 1 ]]; then
  rm -f "$SECRETS"
  rm -rf "${DATA}/repeaterbook"
  log "purged RepeaterBook secrets/work"
fi
log "repeaterbook uninstall complete"
