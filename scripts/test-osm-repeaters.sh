#!/usr/bin/env bash
# Fixture dry-run: build PBFs from OPL, extract OSM, plus RB+RadioID into one publish root.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
FIX="${ROOT}/plugins/osm_repeaters/tests/fixtures"
RB_FIX="${ROOT}/plugins/repeaterbook/tests/fixtures"
RID_FIX="${ROOT}/plugins/radioid/tests/fixtures"

command -v osmium >/dev/null || { echo "osmium required"; exit 1; }
command -v zstd >/dev/null || { echo "zstd required"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PUB="${TMP}/published/repeaters"
EXTRACTS="${TMP}/scratch/extracts"
mkdir -p "$PUB" "$EXTRACTS"

# Separate region PBFs (do not cat overlapping ids into one file).
osmium cat "${FIX}/norway_a.rep.opl" "${FIX}/norway.net.opl" \
  -o "${EXTRACTS}/norway_ostlandet-latest.osm.pbf" --overwrite
osmium cat "${FIX}/norway_b.rep.opl" "${FIX}/norway.net.opl" \
  -o "${EXTRACTS}/norway_vestlandet-latest.osm.pbf" --overwrite
osmium cat "${FIX}/sweden.rep.opl" "${FIX}/sweden.net.opl" \
  -o "${EXTRACTS}/sweden-latest.osm.pbf" --overwrite

export PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

python3 -m plugins.repeaterbook --dry-run-fixtures "$RB_FIX" --publish-root "$PUB"
python3 -m plugins.radioid --dry-run-fixtures "$RID_FIX" --publish-root "$PUB"

python3 - <<PY
import os, sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, "${ROOT}")
os.environ["NAVI_PACK_ROOT"] = "${TMP}"
os.environ["NAVI_EXTRACTS_DIR"] = "${EXTRACTS}"
os.environ["NAVI_REGIONS_CONF"] = "${FIX}/regions.conf"
os.environ["NAVI_OSM_REPEATERS_ENABLED"] = "1"
os.environ["NAVI_OSM_REPEATERS_COUNTRIES"] = "no=europe/norway,se=europe/sweden"
from plugins.osm_repeaters.config import load_config
from plugins.osm_repeaters.run import run_extract

cfg = load_config()
cfg = replace(
    cfg,
    publish_root=Path("${PUB}"),
    work_dir=Path("${TMP}") / "osm_repeaters",
    regions_conf=Path("${FIX}/regions.conf"),
    regions_planet_conf=Path("${FIX}/regions.conf"),
)
rc = run_extract(cfg, generation="20260101T120000Z")
raise SystemExit(rc)
PY

python3 - <<PY
import json
from pathlib import Path
pub = Path("${PUB}")
cur = json.loads((pub / "current.json").read_text())
sources = {s["source"] for s in cur["sources"]}
assert sources == {"repeaterbook", "radioid", "osm"}, sources
osm = next(s for s in cur["sources"] if s["source"] == "osm")
assert "OpenStreetMap" in osm["attribution"]["text"]
assert "ODbL" in (osm.get("licence") or "")
slugs = {c["country_slug"] for c in osm["countries"]}
assert "no" in slugs and "se" in slugs, slugs
man = json.loads(next((pub / "osm" / "no").glob("*/manifest.json")).read_text())
assert man["attribution"]["licence"] == "ODbL-1.0"
env_path = next((pub / "osm" / "no").glob("*/repeaters.json.zst"))
import subprocess
doc = json.loads(subprocess.check_output(["zstd", "-d", "-c", str(env_path)]))
assert doc["attribution"]["licence"] == "ODbL-1.0"
assert "networks" in doc
print("OK osm-repeaters dry-run sources=", sorted(sources), "slugs=", sorted(slugs))
PY
