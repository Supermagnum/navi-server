#!/usr/bin/env bash
# Offline unit checks for geofabrik_replication helpers (no network).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 - "$SCRIPT_DIR/lib" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from geofabrik_replication import seq_to_path, parse_state_txt, normalize_timestamp

assert seq_to_path(4902) == "000/004/902"
assert seq_to_path(4770) == "000/004/770"
text = "# comment\nsequenceNumber=4901\ntimestamp=2026-09-03T20\\:21\\:51Z\n"
seq, ts = parse_state_txt(text)
assert seq == 4901
assert normalize_timestamp(ts) == "2026-09-03T20:21:51Z"
print("ok geofabrik_replication unit checks")
PY
