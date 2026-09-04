#!/usr/bin/env bash
# Update this host's public IPv4 via Dynamic DNS (DuckDNS / Cloudflare / URL).
#
# Config: data/ddns.env (see ddns.env.example). Secrets stay out of config.env.
#
# Usage:
#   ./ddns-update.sh
#   ./ddns-update.sh --force
#   ./ddns-update.sh --check-only   # print public IP + last state; no update

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

FORCE=0
CHECK_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --check-only) CHECK_ONLY=1; shift ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
done

DDNS_ENV="${NAVI_DDNS_ENV:-${NAVI_PACK_ROOT}/ddns.env}"
STATE_FILE="${NAVI_STATE_DIR}/ddns.last_ipv4"

if [[ ! -f "$DDNS_ENV" ]]; then
  die "missing ${DDNS_ENV} — copy scripts/ddns.env.example and set hostname/token"
fi
# shellcheck disable=SC1090
set -a
source "$DDNS_ENV"
set +a

PROVIDER="${NAVI_DDNS_PROVIDER:-}"
HOSTNAME="${NAVI_DDNS_HOSTNAME:-}"
TOKEN="${NAVI_DDNS_TOKEN:-}"
IP_URL="${NAVI_DDNS_IP_URL:-https://api.ipify.org}"
SKIP_UNCHANGED="${NAVI_DDNS_SKIP_UNCHANGED:-1}"
UPDATE_URL="${NAVI_DDNS_UPDATE_URL:-}"
CF_ZONE_ID="${NAVI_DDNS_CF_ZONE_ID:-}"
CF_RECORD_ID="${NAVI_DDNS_CF_RECORD_ID:-}"
CF_PROXIED="${NAVI_DDNS_CF_PROXIED:-false}"
CF_TTL="${NAVI_DDNS_CF_TTL:-120}"

require_cmd curl

[[ -n "$PROVIDER" ]] || die "NAVI_DDNS_PROVIDER is empty in ${DDNS_ENV}"
[[ -n "$HOSTNAME" ]] || die "NAVI_DDNS_HOSTNAME is empty in ${DDNS_ENV}"

discover_ip() {
  local raw
  raw="$(curl -fsS --max-time 20 "$IP_URL" | tr -d '[:space:]')"
  if [[ ! "$raw" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    die "public IP discovery returned non-IPv4: ${raw}"
  fi
  printf '%s\n' "$raw"
}

last_ip=""
[[ -f "$STATE_FILE" ]] && last_ip="$(tr -d '[:space:]' <"$STATE_FILE" || true)"

ip="$(discover_ip)"
log_info "ddns provider=${PROVIDER} host=${HOSTNAME} public_ip=${ip} last_ip=${last_ip:-none}"

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  exit 0
fi

if [[ "$FORCE" -eq 0 && "$SKIP_UNCHANGED" -eq 1 && -n "$last_ip" && "$last_ip" == "$ip" ]]; then
  log_info "ddns unchanged; skipping provider update"
  exit 0
fi

update_duckdns() {
  [[ -n "$TOKEN" ]] || die "NAVI_DDNS_TOKEN required for duckdns"
  local domain="${HOSTNAME%.duckdns.org}"
  domain="${domain%.DUCKDNS.ORG}"
  local resp
  resp="$(curl -fsS --max-time 30 \
    "https://www.duckdns.org/update?domains=${domain}&token=${TOKEN}&ip=${ip}")"
  if [[ "$resp" != "OK" ]]; then
    die "duckdns update failed: response=${resp}"
  fi
  log_info "duckdns update OK domain=${domain} ip=${ip}"
}

update_cloudflare() {
  require_cmd python3
  [[ -n "$TOKEN" ]] || die "NAVI_DDNS_TOKEN required for cloudflare"
  [[ -n "$CF_ZONE_ID" ]] || die "NAVI_DDNS_CF_ZONE_ID required for cloudflare"
  local record_id="$CF_RECORD_ID"
  if [[ -z "$record_id" ]]; then
    record_id="$(curl -fsS --max-time 30 \
      -H "Authorization: Bearer ${TOKEN}" \
      -H "Content-Type: application/json" \
      "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records?type=A&name=${HOSTNAME}" \
      | python3 -c 'import json,sys
d=json.load(sys.stdin)
r=d.get("result") or []
print(r[0]["id"] if r else "")')"
    [[ -n "$record_id" ]] || die "cloudflare: no A record named ${HOSTNAME} in zone ${CF_ZONE_ID}"
    log_info "cloudflare resolved record_id=${record_id}"
  fi
  local body resp
  body="$(HOSTNAME="$HOSTNAME" IP="$ip" TTL="$CF_TTL" PX="$CF_PROXIED" python3 -c 'import json,os
print(json.dumps({
  "type": "A",
  "name": os.environ["HOSTNAME"],
  "content": os.environ["IP"],
  "ttl": int(os.environ["TTL"]),
  "proxied": os.environ["PX"].lower() in ("1", "true", "yes"),
}))')"
  resp="$(curl -fsS --max-time 30 -X PUT \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    --data "$body" \
    "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records/${record_id}")"
  if ! printf '%s' "$resp" | python3 -c 'import json,sys
d=json.load(sys.stdin)
sys.exit(0 if d.get("success") else 1)'; then
    die "cloudflare update failed: ${resp}"
  fi
  log_info "cloudflare update OK host=${HOSTNAME} ip=${ip}"
}

update_url() {
  require_cmd python3
  [[ -n "$UPDATE_URL" ]] || die "NAVI_DDNS_UPDATE_URL required for provider=url"
  local resolved resp
  resolved="$(U="$UPDATE_URL" IP="$ip" H="$HOSTNAME" T="${TOKEN}" python3 -c 'import os,urllib.parse
u=os.environ["U"]
for k, v in (
  ("ip", os.environ["IP"]),
  ("hostname", os.environ["H"]),
  ("token", os.environ.get("T", "")),
):
  u = u.replace("{" + k + "}", urllib.parse.quote(v, safe=""))
print(u)')"
  resp="$(curl -fsS --max-time 30 "$resolved")"
  log_info "url update OK host=${HOSTNAME} ip=${ip} response=${resp}"
}

case "$PROVIDER" in
  duckdns) update_duckdns ;;
  cloudflare) update_cloudflare ;;
  url) update_url ;;
  *) die "unsupported NAVI_DDNS_PROVIDER=${PROVIDER} (want duckdns|cloudflare|url)" ;;
esac

mkdir -p "$(dirname "$STATE_FILE")"
printf '%s\n' "$ip" >"${STATE_FILE}.partial"
mv -f "${STATE_FILE}.partial" "$STATE_FILE"
log_info "ddns state written ${STATE_FILE}=${ip}"
