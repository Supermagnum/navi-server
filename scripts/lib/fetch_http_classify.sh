# HTTP / curl failure classification for extract fetches.
# Sourced by fetch-extracts.sh. Does not run on its own.
#
# Classes:
#   transient   — retry with backoff until NAVI_FETCH_TRANSIENT_BUDGET_SECS
#   needs_human — escalate immediately (404/401/403, etc.)
#   ambiguous   — fail-safe: treat as needs_human

# Globals set by classify_fetch_failure:
#   FETCH_FAIL_CLASS  — transient | needs_human | ambiguous
#   FETCH_FAIL_HTTP   — HTTP code if known, else empty
#   FETCH_FAIL_REASON — short diagnostic string

classify_fetch_failure() {
  local curl_rc="$1"
  local headers_file="${2:-}"
  local http=""

  FETCH_FAIL_CLASS="ambiguous"
  FETCH_FAIL_HTTP=""
  FETCH_FAIL_REASON="curl_rc=${curl_rc}"

  if [[ -n "$headers_file" && -f "$headers_file" ]]; then
    http="$(awk 'BEGIN{IGNORECASE=1}
      /^HTTP\//{code=$2}
      END{print code}' "$headers_file" 2>/dev/null || true)"
    # Last status line wins (redirects); strip CR
    http="${http//$'\r'/}"
  fi
  FETCH_FAIL_HTTP="$http"

  case "$http" in
    502|503|504|408|429)
      FETCH_FAIL_CLASS="transient"
      FETCH_FAIL_REASON="http=${http}"
      return 0
      ;;
    404|401|403)
      FETCH_FAIL_CLASS="needs_human"
      FETCH_FAIL_REASON="http=${http}"
      return 0
      ;;
    304)
      # Handled by caller before classify; not a failure class.
      FETCH_FAIL_CLASS="ambiguous"
      FETCH_FAIL_REASON="http=304 unexpected_in_classify"
      return 0
      ;;
  esac

  # curl exit codes (see curl(1) EXIT CODES)
  case "$curl_rc" in
    6|7|28|52|55|56)
      # DNS fail, couldn't connect, timeout, empty reply, send/recv error
      FETCH_FAIL_CLASS="transient"
      FETCH_FAIL_REASON="curl_rc=${curl_rc} http=${http:-none}"
      return 0
      ;;
    22)
      # HTTP error page with -f; class from status if present, else ambiguous
      if [[ -n "$http" ]]; then
        case "$http" in
          502|503|504|408|429)
            FETCH_FAIL_CLASS="transient"
            FETCH_FAIL_REASON="http=${http} curl_rc=22"
            return 0
            ;;
          404|401|403)
            FETCH_FAIL_CLASS="needs_human"
            FETCH_FAIL_REASON="http=${http} curl_rc=22"
            return 0
            ;;
        esac
      fi
      FETCH_FAIL_CLASS="ambiguous"
      FETCH_FAIL_REASON="curl_rc=22 http=${http:-unknown}"
      return 0
      ;;
  esac

  FETCH_FAIL_CLASS="ambiguous"
  FETCH_FAIL_REASON="curl_rc=${curl_rc} http=${http:-none}"
}

# HEAD (or ranged GET) probe for a single URL. Sets PROBE_HTTP / PROBE_LEN.
probe_url_once() {
  local url="$1"
  local headers tmp
  headers="$(mktemp)"
  tmp="$(mktemp)"
  PROBE_HTTP=""
  PROBE_LEN=""

  set +e
  PROBE_HTTP="$(curl -sS -o /dev/null -D "$headers" -w '%{http_code}' \
    --connect-timeout 30 --max-time 90 -L --head "$url" 2>/dev/null)"
  local rc=$?
  set -e
  if [[ $rc -ne 0 || -z "$PROBE_HTTP" ]]; then
    PROBE_HTTP="000"
  fi
  PROBE_LEN="$(awk 'BEGIN{IGNORECASE=1} /^content-length:/{gsub(/\r/,""); print $2; exit}' "$headers" || true)"

  if [[ -z "$PROBE_LEN" || "$PROBE_LEN" == "0" ]]; then
    set +e
    PROBE_HTTP="$(curl -sS -o "$tmp" -D "$headers" -w '%{http_code}' \
      --connect-timeout 30 --max-time 120 -L -r 0-0 "$url" 2>/dev/null)"
    rc=$?
    set -e
    if [[ $rc -ne 0 || -z "$PROBE_HTTP" ]]; then
      PROBE_HTTP="000"
    fi
    PROBE_LEN="$(awk 'BEGIN{IGNORECASE=1}
      /^content-range:/{
        gsub(/\r/,"")
        if (match($0, /\/([0-9]+)$/)) print substr($0, RSTART+1, RLENGTH-1)
        exit
      }
      /^content-length:/{gsub(/\r/,""); print $2; exit}' "$headers" || true)"
  fi
  rm -f "$headers" "$tmp"
}

# Wait until NEED consecutive clean HEAD probes on url, or until deadline_epoch.
# Returns 0 if recovered, 1 if budget exhausted.
wait_url_recovered() {
  local url="$1"
  local deadline_epoch="$2"
  local need="${3:-${NAVI_FETCH_RECOVERY_CLEAN_NEED:-3}}"
  local interval="${4:-${NAVI_FETCH_RECOVERY_INTERVAL_SECS:-30}}"
  local streak=0
  local round=0
  local now sleep_for

  while true; do
    now="$(date +%s)"
    if [[ "$now" -ge "$deadline_epoch" ]]; then
      log_warn "fetch recovery budget exhausted url=${url} clean_streak=${streak}/${need}"
      return 1
    fi
    round=$((round + 1))
    probe_url_once "$url"
    if [[ "$PROBE_HTTP" == "200" && -n "$PROBE_LEN" && "$PROBE_LEN" != "0" ]]; then
      streak=$((streak + 1))
      log_info "fetch recovery probe ok url=${url} round=${round} http=${PROBE_HTTP} content_length=${PROBE_LEN} clean_streak=${streak}/${need}"
      if [[ "$streak" -ge "$need" ]]; then
        log_info "fetch recovery STABLE url=${url} after ${need} consecutive clean probes"
        return 0
      fi
    else
      if [[ "$streak" -gt 0 ]]; then
        log_warn "fetch recovery streak reset url=${url} was=${streak} http=${PROBE_HTTP}"
      else
        log_warn "fetch recovery probe fail url=${url} round=${round} http=${PROBE_HTTP}"
      fi
      streak=0
    fi
    now="$(date +%s)"
    sleep_for="$interval"
    if [[ $((now + sleep_for)) -gt "$deadline_epoch" ]]; then
      sleep_for=$((deadline_epoch - now))
      [[ "$sleep_for" -gt 0 ]] || return 1
    fi
    log_info "fetch recovery sleep ${sleep_for}s until next probe"
    sleep "$sleep_for"
  done
}
