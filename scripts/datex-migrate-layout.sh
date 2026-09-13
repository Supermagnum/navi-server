#!/usr/bin/env bash
# Live DATEX multi-provider cutover (requires root).
# Assumes repo code is already updated (datex_common + npra + apache conf).
#
#   sudo /media/navi/navi-server/scripts/datex-migrate-layout.sh
#
# Steps: copy README if present under /tmp/datex-mp, migrate flat
# published/datex/* into published/datex/npra/, refresh providers.json,
# install Apache site conf, reload apache2, optionally kick the NPRA poller.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${ROOT}/data"
SERVICE_USER="${NAVI_SERVICE_USER:-navit-server}"
APACHE_DST="/etc/apache2/sites-available/apache-navi-packs.conf"

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "INFO" "$*"; }
fail() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "FAILED" "$*"; exit 1; }

[[ "$(id -u)" -eq 0 ]] || fail "re-run with sudo"

if [[ -f /tmp/datex-mp/README.md ]]; then
  cp -a /tmp/datex-mp/README.md "${ROOT}/README.md"
  chown "${SERVICE_USER}:${SERVICE_USER}" "${ROOT}/README.md"
  log "updated README.md"
fi

flat="${DATA}/published/datex"
npra="${flat}/npra"
mkdir -p "$npra"
for f in GetSituation.xml GetTravelTimeData.xml GetMeasuredWeatherData.xml \
         GetCCTVSiteTable.xml source.json; do
  if [[ -f "${flat}/${f}" && ! -e "${npra}/${f}" ]]; then
    mv "${flat}/${f}" "${npra}/${f}"
    log "migrated published/datex/${f} -> datex/npra/${f}"
  elif [[ -f "${flat}/${f}" && -e "${npra}/${f}" ]]; then
    rm -f "${flat}/${f}"
    log "removed leftover flat published/datex/${f}"
  fi
done

PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}" \
  NAVI_PACK_ROOT="${DATA}" \
  python3 -c '
import os
from pathlib import Path
from plugins.datex_common.providers_index import rebuild_providers_index
print(rebuild_providers_index(Path(os.environ["NAVI_PACK_ROOT"])))
'
chown -R "${SERVICE_USER}:${SERVICE_USER}" "$flat"
chmod -R a+rX "$flat"
log "refreshed providers.json"

[[ -f "${ROOT}/http/apache-navi-packs.conf" ]] || fail "missing apache conf"
cp "${ROOT}/http/apache-navi-packs.conf" "$APACHE_DST"
apache2ctl configtest
systemctl reload apache2
log "Apache reloaded"

if systemctl is-enabled navi-datex-npra.timer >/dev/null 2>&1; then
  systemctl start navi-datex-npra.service \
    || log "WARN: datex poll start failed — check journalctl -u navi-datex-npra.service"
fi

log "Verify redirects:"
curl -sI http://127.0.0.1/datex/GetSituation.xml | tr -d '\r' | grep -Ei '^(HTTP|Location):' || true
curl -sI http://127.0.0.1/datex/npra/source.json | tr -d '\r' | head -n1 || true
curl -sI http://127.0.0.1/datex/providers.json | tr -d '\r' | head -n1 || true
log "DATEX layout migrate complete"
