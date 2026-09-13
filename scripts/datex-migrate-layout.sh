#!/usr/bin/env bash
# Live DATEX multi-provider cutover (requires root).
# Assumes repo code is already updated (datex_common + npra + apache conf).
#
#   sudo /media/navi/navi-server/scripts/datex-migrate-layout.sh
#
# Steps: copy README if present under /tmp/datex-mp, migrate flat
# published/datex/* into published/datex/npra/, refresh providers.json,
# install shared DATEX rewrite snippet into both :80 and :443, reload
# apache2, optionally kick the NPRA poller.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="${ROOT}/data"
SERVICE_USER="${NAVI_SERVICE_USER:-navit-server}"
APACHE_DST="/etc/apache2/sites-available/apache-navi-packs.conf"
DATEX_REWRITE_SRC="${ROOT}/http/apache-navi-datex-rewrites.conf"
DATEX_REWRITE_DST="/etc/apache2/conf-available/apache-navi-datex-rewrites.conf"
COMMON_CONF="/etc/apache2/conf-available/navi-packs-common.conf"
INCLUDE_LINE="Include /etc/apache2/conf-available/apache-navi-datex-rewrites.conf"

log() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "INFO" "$*"; }
fail() { printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "FAILED" "$*"; exit 1; }

ensure_common_includes_datex_rewrites() {
  local conf="$1"
  local inc="$2"
  if [[ ! -f "$conf" ]]; then
    log "WARN: ${conf} missing — :443 DATEX redirects not installed"
    return 0
  fi
  python3 - "$conf" "$inc" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
inc = sys.argv[2].rstrip() + "\n"
text = path.read_text(encoding="utf-8")
if inc.strip() in text:
    print("already-present")
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
print("inserted")
PY
}

install_datex_apache_rewrites() {
  [[ -f "$DATEX_REWRITE_SRC" ]] || fail "missing ${DATEX_REWRITE_SRC}"
  [[ -f "${ROOT}/http/apache-navi-packs.conf" ]] || fail "missing apache conf"
  cp "$DATEX_REWRITE_SRC" "$DATEX_REWRITE_DST"
  log "installed ${DATEX_REWRITE_DST}"
  ensure_common_includes_datex_rewrites "$COMMON_CONF" "$INCLUDE_LINE"
  log "HTTPS common conf DATEX include: ${COMMON_CONF}"
  cp "${ROOT}/http/apache-navi-packs.conf" "$APACHE_DST"
  log "installed ${APACHE_DST}"
}

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

install_datex_apache_rewrites
a2enmod rewrite >/dev/null
apache2ctl configtest
systemctl reload apache2
log "Apache reloaded (:80 site + :443 common DATEX rewrites)"

if systemctl is-enabled navi-datex-npra.timer >/dev/null 2>&1; then
  systemctl start navi-datex-npra.service \
    || log "WARN: datex poll start failed — check journalctl -u navi-datex-npra.service"
fi

log "Verify redirects:"
curl -sI http://127.0.0.1/datex/GetSituation.xml | tr -d '\r' | grep -Ei '^(HTTP|Location):' || true
curl -sI http://127.0.0.1/datex/source.json | tr -d '\r' | grep -Ei '^(HTTP|Location):' || true
curl -sI http://127.0.0.1/datex/npra/source.json | tr -d '\r' | head -n1 || true
curl -sI http://127.0.0.1/datex/providers.json | tr -d '\r' | head -n1 || true
if [[ -f /etc/apache2/sites-enabled/default-ssl.conf ]]; then
  curl -skI --resolve navigate-me.duckdns.org:443:127.0.0.1 \
    https://navigate-me.duckdns.org/datex/source.json \
    | tr -d '\r' | grep -Ei '^(HTTP|Location):' || true
  curl -skI --resolve navigate-me.duckdns.org:443:127.0.0.1 \
    https://navigate-me.duckdns.org/datex/GetSituation.xml \
    | tr -d '\r' | grep -Ei '^(HTTP|Location):' || true
fi
log "DATEX layout migrate complete"
