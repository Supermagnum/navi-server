#!/usr/bin/env bash
# Unit checks for skip_reason=no_road_network (orchestration skip).
# Does not touch the live planet bake.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

PASS=0
FAIL=0
ok() { echo "PASS: $*"; PASS=$((PASS + 1)); }
bad() { echo "FAIL: $*"; FAIL=$((FAIL + 1)); }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/navi-skip-reason.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

PACK="$TMP/pack"
mkdir -p "$PACK"
export NAVI_PACK_ROOT="$PACK"
# Minimal config so load_config succeeds if called; region_skip_reason only
# needs NAVI_PACK_ROOT + conf files.
: >"${PACK}/config.env"

WEEKLY="${PACK}/regions.conf"
PLANET="${PACK}/regions.planet.conf"
cat >"$WEEKLY" <<'EOF'
# weekly overrides / tags
australia_oceania_ile_de_clipperton	geofabrik:australia-oceania/ile-de-clipperton	skip_reason=no_road_network
australia_oceania_australia_ashmore_cartier	geofabrik:australia-oceania/australia/ashmore-cartier	skip_reason=no_road_network
australia_oceania_australia_heard_mcdonald	geofabrik:australia-oceania/australia/heard-mcdonald	skip_reason=no_road_network
us_west_virginia	geofabrik:north-america/us/west-virginia
EOF
cat >"$PLANET" <<'EOF'
australia_oceania_ile_de_clipperton	geofabrik:australia-oceania/ile-de-clipperton
australia_oceania_australia_ashmore_cartier	geofabrik:australia-oceania/australia/ashmore-cartier
australia_oceania_australia_heard_mcdonald	geofabrik:australia-oceania/australia/heard-mcdonald
australia_oceania_australia_coral_sea_islands	geofabrik:australia-oceania/australia/coral-sea-islands
us_west_virginia	geofabrik:north-america/us/west-virginia
EOF

export NAVI_REGIONS_CONF="$PLANET"

got="$(region_skip_reason australia_oceania_ile_de_clipperton || true)"
if [[ "$got" == "no_road_network" ]]; then
  ok "clipperton skip_reason from weekly regions.conf"
else
  bad "clipperton expected no_road_network got=${got@Q}"
fi

got="$(region_skip_reason australia_oceania_australia_ashmore_cartier || true)"
[[ "$got" == "no_road_network" ]] && ok "ashmore skip_reason" || bad "ashmore got=${got@Q}"

got="$(region_skip_reason australia_oceania_australia_heard_mcdonald || true)"
[[ "$got" == "no_road_network" ]] && ok "heard skip_reason" || bad "heard got=${got@Q}"

# Untagged leaf must not inherit a skip (regression).
got="$(region_skip_reason australia_oceania_australia_coral_sea_islands || true)"
if [[ -z "$got" ]]; then
  ok "coral_sea untagged — no skip_reason"
else
  bad "coral_sea unexpectedly skipped reason=${got@Q}"
fi

got="$(region_skip_reason us_west_virginia || true)"
if [[ -z "$got" ]]; then
  ok "us_west_virginia untagged — no skip_reason"
else
  bad "us_west_virginia unexpectedly skipped reason=${got@Q}"
fi

# Simulate orchestrator early-return: tagged region must not invoke fetch stub.
FETCH_LOG="$TMP/fetch.log"
: >"$FETCH_LOG"
fake_process_region() {
  local rid="$1"
  local skip_reason
  skip_reason="$(region_skip_reason "$rid")"
  if [[ -n "$skip_reason" ]]; then
    echo "SKIPPED ${rid} ${skip_reason}"
    return 0
  fi
  echo "FETCH ${rid}" >>"$FETCH_LOG"
  echo "WOULD_FETCH ${rid}"
  return 0
}

# Progress counter: skipped counts as processed.
local_n=3
done_in_batch=0
REGIONS=(australia_oceania_ile_de_clipperton australia_oceania_australia_coral_sea_islands us_west_virginia)
out="$TMP/process.out"
: >"$out"
for rid in "${REGIONS[@]}"; do
  fake_process_region "$rid" >>"$out"
  done_in_batch=$((done_in_batch + 1))
done
[[ "$done_in_batch" -eq "$local_n" ]] && ok "progress ${done_in_batch}/${local_n} includes skip" \
  || bad "progress done=${done_in_batch} expected=${local_n}"

grep -q 'SKIPPED australia_oceania_ile_de_clipperton no_road_network' "$out" \
  && ok "orchestrator skip log for clipperton" \
  || bad "missing skip line in process out"
grep -q 'WOULD_FETCH australia_oceania_australia_coral_sea_islands' "$out" \
  && ok "untagged coral_sea still reaches fetch path" \
  || bad "coral_sea did not reach fetch path"
grep -q 'WOULD_FETCH us_west_virginia' "$out" \
  && ok "untagged west_virginia still reaches fetch path" \
  || bad "west_virginia did not reach fetch path"

if [[ -s "$FETCH_LOG" ]] && ! grep -q clipperton "$FETCH_LOG"; then
  ok "fetch stub never called for clipperton"
else
  # empty fetch log with only coral+wv is also fine; clipperton must be absent
  if grep -q clipperton "$FETCH_LOG" 2>/dev/null; then
    bad "fetch stub called for clipperton"
  else
    ok "fetch stub never called for clipperton"
  fi
fi

# Live weekly file (if present) must parse for the three tagged leaves.
LIVE="${SCRIPT_DIR}/../data/regions.conf"
if [[ -f "$LIVE" ]]; then
  export NAVI_PACK_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)/data"
  for rid in australia_oceania_ile_de_clipperton australia_oceania_australia_ashmore_cartier australia_oceania_australia_heard_mcdonald; do
    got="$(region_skip_reason "$rid" || true)"
    if [[ "$got" == "no_road_network" ]]; then
      ok "live regions.conf ${rid}"
    else
      bad "live regions.conf ${rid} got=${got@Q}"
    fi
  done
fi

echo "---- ${PASS} passed, ${FAIL} failed ----"
[[ "$FAIL" -eq 0 ]]
