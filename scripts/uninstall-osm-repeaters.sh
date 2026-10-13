#!/usr/bin/env bash
# Disable OSM repeaters extract and remove published/repeaters/osm/.
set -euo pipefail
NAVI_SERVER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${NAVI_SERVER_ROOT}/data"
PUB_SRC="${DATA}/published/repeaters/osm"

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
  echo "== uninstall-osm-repeaters check =="
  [[ -d "$PUB_SRC" ]] && echo "PRESENT $PUB_SRC" || echo "ABSENT published osm"
  [[ -d "${DATA}/osm_repeaters" ]] && echo "PRESENT work dir" || echo "ABSENT work dir"
  exit 0
fi
[[ "$(id -u)" -eq 0 ]] || fail "requires root"

CFG="${DATA}/config.env"
if [[ -f "$CFG" ]]; then
  if grep -q '^NAVI_OSM_REPEATERS_ENABLED=' "$CFG" 2>/dev/null; then
    sed -i 's/^NAVI_OSM_REPEATERS_ENABLED=.*/NAVI_OSM_REPEATERS_ENABLED=0/' "$CFG"
  else
    printf '%s\n' 'NAVI_OSM_REPEATERS_ENABLED=0' >>"$CFG"
  fi
fi
rm -rf "$PUB_SRC"
if [[ -f "${DATA}/published/repeaters/current.json" ]]; then
  CUR="${DATA}/published/repeaters/current.json" python3 - <<'PY' || true
import json, os
from pathlib import Path
p = Path(os.environ["CUR"])
doc = json.loads(p.read_text())
doc["sources"] = [s for s in doc.get("sources") or [] if s.get("source") != "osm"]
p.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
PY
fi
if [[ "$PURGE" -eq 1 ]]; then
  rm -rf "${DATA}/osm_repeaters"
  log "purged data/osm_repeaters"
fi
log "osm_repeaters uninstall complete"
