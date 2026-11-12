#!/usr/bin/env bash
# Cheap daily check: fetch maps.json; if CoMaps latest differs from published
# current.json, exit 10 so the timer/service can trigger a bake.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

python3 - "$NAVI_MWM_PUBLISHED_ROOT" "$NAVI_MWM_METASERVER" "$NAVI_MWM_MAP_SERIES" "$SCRIPT_DIR" <<'PY'
import json, os, sys
from pathlib import Path
sys.path.insert(0, os.path.join(sys.argv[4], "lib"))
from comaps_release import http_get_bytes, parse_maps_json

published = Path(sys.argv[1])
meta = sys.argv[2].rstrip("/")
series = sys.argv[3]
raw = http_get_bytes(f"{meta}/meta/maps.json", timeout=30.0)
latest = parse_maps_json(raw, series)
current = None
cur = published / "current.json"
if cur.is_file():
    try:
        body = json.loads(cur.read_text())
        current = (body.get("comaps") or {}).get("release_version") or body.get("version")
        if current is not None:
            current = int(current)
    except Exception:
        current = None
print(json.dumps({"latest": latest, "current": current, "map_series": series}))
if current is None or int(current) != int(latest):
    raise SystemExit(10)
raise SystemExit(0)
PY
