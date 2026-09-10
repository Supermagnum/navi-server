#!/usr/bin/env bash
# Offline unit checks for geofabrik_replication helpers (no network).
# Includes SEQUENCE_TIP verify helpers + osmium round-trip when available.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PASS=0
FAIL=0
assert_eq() {
  local got="$1" want="$2" name="$3"
  if [[ "$got" == "$want" ]]; then
    echo "PASS $name"
    PASS=$((PASS + 1))
  else
    echo "FAIL $name got=${got@Q} want=${want@Q}"
    FAIL=$((FAIL + 1))
  fi
}

python3 - "$SCRIPT_DIR/lib" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from geofabrik_replication import (
    seq_to_path,
    parse_state_txt,
    normalize_timestamp,
    sequence_matches_tip,
)

assert seq_to_path(4902) == "000/004/902"
assert seq_to_path(4770) == "000/004/770"
text = "# comment\nsequenceNumber=4901\ntimestamp=2026-09-03T20\\:21\\:51Z\n"
seq, ts = parse_state_txt(text)
assert seq == 4901
assert normalize_timestamp(ts) == "2026-09-03T20:21:51Z"
assert sequence_matches_tip(4902, 4902) is True
assert sequence_matches_tip(4901, 4902) is False
print("ok geofabrik_replication unit checks")
PY
assert_eq "$?" "0" "pure helpers"

# osmium round-trip: verify_pbf_reaches_tip PASS on matching header, FAIL on mismatch.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
xml="$TMP/t.osm"
printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>' \
  '<osm version="0.6" generator="test"></osm>' >"$xml"
pbf_ok="$TMP/ok.osm.pbf"
pbf_bad="$TMP/bad.osm.pbf"
osmium cat "$xml" -f pbf -o "$pbf_ok" --overwrite \
  --output-header=osmosis_replication_base_url=https://example.test/updates \
  --output-header=osmosis_replication_sequence_number=4902 \
  --output-header=osmosis_replication_timestamp=2026-09-03T20:21:51Z
osmium cat "$xml" -f pbf -o "$pbf_bad" --overwrite \
  --output-header=osmosis_replication_base_url=https://example.test/updates \
  --output-header=osmosis_replication_sequence_number=4899 \
  --output-header=osmosis_replication_timestamp=2026-09-01T00:00:00Z

python3 - "$SCRIPT_DIR/lib" "$pbf_ok" "$pbf_bad" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from geofabrik_replication import ReplicationState, verify_pbf_reaches_tip

tip = ReplicationState(
    sequence=4902,
    timestamp="2026-09-03T20:21:51Z",
    base_url="https://example.test/updates",
)
verify_pbf_reaches_tip(Path(sys.argv[2]), tip)
try:
    verify_pbf_reaches_tip(Path(sys.argv[3]), tip)
except ValueError as e:
    assert "SEQUENCE_TIP_MISMATCH" in str(e), e
else:
    raise SystemExit("expected SEQUENCE_TIP_MISMATCH for bad PBF")
print("ok verify_pbf_reaches_tip")
PY
assert_eq "$?" "0" "verify_pbf_reaches_tip osmium round-trip"

if [[ "$FAIL" -ne 0 ]]; then
  echo "FAILED ${FAIL} checks (${PASS} passed)" >&2
  exit 1
fi
echo "ALL PASS (${PASS})"
