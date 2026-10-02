#!/usr/bin/env bash
# Isolated tests for Δh missing-edge convert skip + validate regression.
# Does not touch the live published tree or elevation cache.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

PASS=0
FAIL=0
ok() { echo "PASS: $*"; PASS=$((PASS + 1)); }
bad() { echo "FAIL: $*"; FAIL=$((FAIL + 1)); }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/navi-delta-h-guard.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

PACK="$TMP/pack"
mkdir -p \
  "$PACK/logs" "$PACK/scratch/convert" "$PACK/scratch/extracts" \
  "$PACK/staging" "$PACK/generations" "$PACK/published/packs" \
  "$PACK/elevation/copernicus" "$PACK/elevation_empty/copernicus"

# Minimal regions.conf + bbox for two fake regions.
cat >"$PACK/regions.conf" <<'EOF'
fake_ok	geofabrik:europe/fake-ok
fake_empty	geofabrik:europe/fake-empty
EOF
cat >"$PACK/regions.conf.bboxes.json" <<'EOF'
{
  "fake_ok": [59.0, 5.0, 59.5, 5.5],
  "fake_empty": [10.0, 10.0, 10.5, 10.5]
}
EOF

# One DEM cell covering fake_ok only.
mkdir -p "$PACK/elevation/copernicus/N59E005"
printf 'dem' >"$PACK/elevation/copernicus/N59E005/Copernicus_DSM_COG_10_N59_00_E005_00_DEM.tif"

export NAVI_PACK_ROOT="$PACK"
unset NAVI_PACK_CONFIG
export NAVI_REGIONS_CONF="$PACK/regions.conf"
export NAVI_PUBLISHED_DIR="$PACK/published"
export NAVI_ELEV_DIR="$PACK/elevation"
export NAVI_BAKE_DELTA_H=1
export NAVI_SCRATCH_DIR="$PACK/scratch"
export NAVI_EXTRACTS_DIR="$PACK/scratch/extracts"
export NAVI_CONVERT_DIR="$PACK/scratch/convert"
export NAVI_LOG_DIR="$PACK/logs"
export NAVI_STAGING_DIR="$PACK/staging"
export NAVI_GENERATIONS_DIR="$PACK/generations"
# Avoid sun-order requiring real conf layout beyond bbox.
export NAVI_BAKE_SUN_ORDER=0

# --- dem_coverage unit ---
if python3 "${SCRIPT_DIR}/test-dem-coverage.py" -q; then
  ok "dem_coverage unit"
else
  bad "dem_coverage unit"
fi

# --- convert skips uncovered region, continues ---
# Stub convert binary where resolve_convert_bin looks (NAVI_CONVERT_BIN is ignored).
STUB_BIN="${SCRIPT_DIR}/../target/release/navi-indexed-convert"
mkdir -p "$(dirname "$STUB_BIN")"
cat >"$STUB_BIN" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
out=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-dir) out="$2"; shift 2 ;;
    --pbf|--profiles|--elev-dir) shift 2 ;;
    *) shift ;;
  esac
done
mkdir -p "$out"
rid="$(basename "$out")"
cat >"${out}/${rid}-latest.navi-manifest.json" <<EOF
{
  "schema": 1,
  "stem": "${rid}-latest",
  "has_delta_h": true,
  "delta_h_missing_edges": 10,
  "graph_tiles": {"car": [{"file": "${rid}-latest.navi-graph-car.t0_0.rkyv"}]},
  "poi_barrier_file": "${rid}-latest.navi-poi-barrier.rkyv",
  "wetland_file": "${rid}-latest.navi-wetland.rkyv"
}
EOF
python3 - "$out" "$rid" <<'PY'
import sys
from pathlib import Path
out, rid = Path(sys.argv[1]), sys.argv[2]
def write(name, magic):
    p = out / name
    p.write_bytes(magic.to_bytes(4, "little") + (9).to_bytes(4, "little") + b"\0" * 256 * 1024)
write(f"{rid}-latest.navi-graph-car.t0_0.rkyv", 0x4E56524B)
write(f"{rid}-latest.navi-poi-barrier.rkyv", 0x4E565042)
write(f"{rid}-latest.navi-wetland.rkyv", 0x4E56574C)
PY
EOS
chmod +x "$STUB_BIN"
# Widen size bands so tiny stub packs do not FLAG.
export NAVI_SIZE_GRAPH_MIN_RATIO=0
export NAVI_SIZE_POI_MIN_RATIO=0
export NAVI_SIZE_TOTAL_MIN_RATIO=0
export NAVI_SIZE_WETLAND_MIN_RATIO=0
export NAVI_SIZE_GRAPH_MAX_RATIO=100
export NAVI_SIZE_POI_MAX_RATIO=100
export NAVI_SIZE_TOTAL_MAX_RATIO=100
export NAVI_SIZE_WETLAND_MAX_RATIO=100

# Need PBFs present for both (convert soft-skips missing extract).
: >"$PACK/scratch/extracts/fake_ok-latest.osm.pbf"
: >"$PACK/scratch/extracts/fake_empty-latest.osm.pbf"
# Non-empty PBF for size checks later
dd if=/dev/zero of="$PACK/scratch/extracts/fake_ok-latest.osm.pbf" bs=1024 count=100 status=none
dd if=/dev/zero of="$PACK/scratch/extracts/fake_empty-latest.osm.pbf" bs=1024 count=100 status=none

set +e
"${SCRIPT_DIR}/convert-region.sh" --all >"$TMP/convert.out" 2>&1
crc=$?
set -e
if [[ "$crc" -eq 0 ]]; then
  ok "convert --all exits 0 with mixed DEM coverage"
else
  bad "convert --all exit=$crc (want 0)"
fi
if rg -q 'dem_coverage' "$TMP/convert.out"; then
  ok "convert logged dem_coverage skip"
else
  bad "convert did not log dem_coverage skip"
fi
if [[ -d "$PACK/scratch/convert/fake_ok" ]]; then
  ok "fake_ok converted"
else
  bad "fake_ok missing convert output"
fi
if [[ ! -d "$PACK/scratch/convert/fake_empty" ]]; then
  ok "fake_empty skipped (no convert dir)"
else
  bad "fake_empty should not have convert output"
fi

# --- validate regression vs published baseline ---
mkdir -p "$PACK/published/packs/europe/fake-ok/20260101T000000Z-test"
# Prior published baseline with low missing.
cat >"$PACK/published/packs/europe/fake-ok/20260101T000000Z-test/manifest.json" <<'EOF'
{"generation":"20260101T000000Z-test","bake_id":"fake_ok","has_delta_h":true}
EOF
cat >"$PACK/published/packs/europe/fake-ok/20260101T000000Z-test/fake_ok-latest.navi-manifest.json" <<'EOF'
{
  "schema": 1,
  "stem": "fake_ok-latest",
  "has_delta_h": true,
  "delta_h_missing_edges": 10
}
EOF

# Blow up missing in convert scratch and wrap for validate.
python3 - <<'PY'
import json
from pathlib import Path
import os
p = Path(os.environ["NAVI_CONVERT_DIR"]) / "fake_ok" / "fake_ok-latest.navi-manifest.json"
man = json.loads(p.read_text())
man["delta_h_missing_edges"] = 5_000_000
p.write_text(json.dumps(man, indent=2) + "\n")
PY

set +e
NAVI_PUBLISHED_DIR="$PACK/published" \
  "${SCRIPT_DIR}/validate-packs.sh" --from-convert --region fake_ok \
  >"$TMP/validate.out" 2>&1
vrc=$?
set -e
if [[ "$vrc" -ne 0 ]] && rg -q 'delta_h_missing_edges regression' "$TMP/validate.out"; then
  ok "validate fails on delta_h_missing regression"
else
  bad "validate should fail regression (rc=$vrc)"
  cat "$TMP/validate.out" || true
fi

# Published baseline untouched.
if [[ -f "$PACK/published/packs/europe/fake-ok/20260101T000000Z-test/fake_ok-latest.navi-manifest.json" ]]; then
  ok "previous published generation still present"
else
  bad "published baseline missing"
fi

# --- first-gen abs rule ---
rm -rf "$PACK/published/packs/europe/fake-ok"
python3 - <<'PY'
import json
from pathlib import Path
import os
p = Path(os.environ["NAVI_CONVERT_DIR"]) / "fake_ok" / "fake_ok-latest.navi-manifest.json"
man = json.loads(p.read_text())
man["delta_h_missing_edges"] = 60_000
p.write_text(json.dumps(man, indent=2) + "\n")
PY
set +e
NAVI_PUBLISHED_DIR="$PACK/published" \
  "${SCRIPT_DIR}/validate-packs.sh" --from-convert --region fake_ok \
  >"$TMP/validate-first.out" 2>&1
vrc=$?
set -e
if [[ "$vrc" -ne 0 ]] && rg -q 'first-gen delta_h_missing' "$TMP/validate-first.out"; then
  ok "validate fails first-gen abs rule"
else
  bad "first-gen abs rule (rc=$vrc)"
  cat "$TMP/validate-first.out" || true
fi

echo "----"
echo "PASS=${PASS} FAIL=${FAIL}"
[[ "$FAIL" -eq 0 ]]
