#!/usr/bin/env bash
# Remove RadioID mirror flags and published/repeaters/radioid/.
# Shared timer removed only when RepeaterBook is also inactive.
set -euo pipefail
NAVI_SERVER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${NAVI_SERVER_ROOT}/data"
SYSTEMD_DIR="/etc/systemd/system"
UNITS=(navi-repeaterbook.timer navi-repeaterbook.service)
SECRETS="${DATA}/secrets/radioid.env"
PUB_SRC="${DATA}/published/repeaters/radioid"

PURGE=0
CHECK_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge) PURGE=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "INFO" "$*"; }
fail() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "FAILED" "$*"; exit 1; }

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  echo "== uninstall-radioid check =="
  [[ -f "$SECRETS" ]] && echo "PRESENT secrets" || echo "ABSENT secrets"
  [[ -d "$PUB_SRC" ]] && echo "PRESENT $PUB_SRC" || echo "ABSENT published radioid"
  exit 0
fi
[[ "$(id -u)" -eq 0 ]] || fail "requires root"

rb_on=0
grep -q '^NAVI_REPEATERBOOK_ENABLED=1' "${DATA}/config.env" 2>/dev/null && rb_on=1 || true
[[ -d "${DATA}/published/repeaters/repeaterbook" ]] && rb_on=1 || true
if [[ "$rb_on" -eq 0 ]]; then
  systemctl disable --now navi-repeaterbook.timer 2>/dev/null || true
  for u in "${UNITS[@]}"; do rm -f "${SYSTEMD_DIR}/${u}"; done
  systemctl daemon-reload 2>/dev/null || true
  log "removed shared navi-repeaterbook timer/service"
else
  log "kept navi-repeaterbook timer (RepeaterBook still present/enabled)"
fi

CFG="${DATA}/config.env"
if [[ -f "$CFG" ]]; then
  sed -i \
    -e 's/^NAVI_RADIOID_ENABLED=.*/NAVI_RADIOID_ENABLED=0/' \
    -e 's/^RADIOID_PERMISSION_CONFIRMED=.*/RADIOID_PERMISSION_CONFIRMED=0/' \
    "$CFG" || true
fi
rm -rf "$PUB_SRC"
if [[ -f "${DATA}/published/repeaters/current.json" ]]; then
  CUR="${DATA}/published/repeaters/current.json" python3 - <<'PY' || true
import json, os
from pathlib import Path
p=Path(os.environ["CUR"])
doc=json.loads(p.read_text())
doc["sources"]=[s for s in doc.get("sources") or [] if s.get("source")!="radioid"]
p.write_text(json.dumps(doc, indent=2, sort_keys=True)+"\n")
PY
fi
if [[ "$PURGE" -eq 1 ]]; then
  rm -f "$SECRETS"
  rm -rf "${DATA}/radioid"
  log "purged RadioID secrets/work"
fi
log "radioid uninstall complete"
