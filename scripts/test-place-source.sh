#!/usr/bin/env bash
# Place-source switch-off + manifest staging tests (no live tree).
# Usage: ./test-place-source.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); echo "PASS: $*"; }
fail() { FAIL=$((FAIL + 1)); echo "FAIL: $*"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PACK="$TMP/pack"
mkdir -p "$PACK/logs" "$PACK/scratch/convert" "$PACK/scratch/extracts" \
  "$PACK/state" "$PACK/staging" "$PACK/generations" "$PACK/published/packs" \
  "$PACK/elev"
cat >"$PACK/config.env" <<EOF
NAVI_PACK_ROOT=$PACK
NAVI_REGIONS_CONF=$PACK/regions.conf
NAVI_PROFILES=car
NAVI_BAKE_DELTA_H=0
NAVI_PREFETCH_DEM=0
NAVI_BAKE_PLACE_SOURCE=0
NAVI_BAKE_SUN_ORDER=0
NAVI_PUBLISHED_KEEP_PER_REGION=1
EOF
cat >"$PACK/regions.conf" <<'EOF'
fake_place	url:https://example.invalid/fake.osm.pbf	geofabrik:europe/fake-place
EOF

# Minimal convert out (packs only — no place-source).
rid=fake_place
out="$PACK/scratch/convert/${rid}"
mkdir -p "$out"
cat >"${out}/${rid}-latest.navi-manifest.json" <<EOF
{
  "schema": 1,
  "stem": "${rid}-latest",
  "has_delta_h": false,
  "graph_format_version": 9,
  "graph_tiles": {"car": [{"file": "${rid}-latest.navi-graph-car.t0_0.rkyv"}]},
  "poi_barrier_file": "${rid}-latest.navi-poi-barrier.rkyv",
  "wetland_file": "${rid}-latest.navi-wetland.rkyv"
}
EOF
python3 - "$out" "$rid" <<'PY'
import struct, sys
from pathlib import Path
out, rid = Path(sys.argv[1]), sys.argv[2]
def write(name, magic):
    (out / name).write_bytes(struct.pack("<II", magic, 9) + b"\0" * 64)
write(f"{rid}-latest.navi-graph-car.t0_0.rkyv", 0x4E56524B)
write(f"{rid}-latest.navi-poi-barrier.rkyv", 0x4E565042)
write(f"{rid}-latest.navi-wetland.rkyv", 0x4E56574C)
PY
before="$(cd "$out" && find . -type f -printf '%P %s\n' | sort | sha256sum | awk '{print $1}')"

export NAVI_PACK_CONFIG="$PACK/config.env"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

# 1) Switch off: build-place-source exits 0 and writes nothing.
"${SCRIPT_DIR}/build-place-source.sh" --region "$rid"
after="$(cd "$out" && find . -type f -printf '%P %s\n' | sort | sha256sum | awk '{print $1}')"
if [[ "$before" == "$after" ]]; then
  pass "switch off leaves convert out byte-identical"
else
  fail "switch off changed convert out"
fi

# 2) With switch on + fixture binary path, place-source file is staged by publish assemble.
if [[ -x "${ROOT}/target/release/place-source-points" ]] || [[ -x "${ROOT}/target/debug/place-source-points" ]]; then
  :
else
  (cd "$ROOT" && cargo build -q -p place-source-points) || true
fi
BIN=""
for cand in "${ROOT}/target/release/place-source-points" "${ROOT}/target/debug/place-source-points"; do
  [[ -x "$cand" ]] && BIN="$cand" && break
done

FIX="${ROOT}/place-source-points/tests/fixtures/place-mini.osm.pbf"
if [[ -n "$BIN" && -f "$FIX" ]] && command -v osmium >/dev/null 2>&1; then
  cp -a "$FIX" "$PACK/scratch/extracts/${rid}-latest.osm.pbf"
  # Explicit pack root + switch for the child (config.env alone is enough when
  # NAVI_PACK_CONFIG is set; pass overrides so the test cannot see the clone tree).
  NAVI_PACK_CONFIG="$PACK/config.env" \
  NAVI_PACK_ROOT="$PACK" \
  NAVI_BAKE_PLACE_SOURCE=1 \
  NAVI_PLACE_SOURCE_BIN="$BIN" \
    "${SCRIPT_DIR}/build-place-source.sh" --region "$rid"
  ps_file="${out}/${rid}-latest.navi-place-source.osm.pbf"
  if [[ -s "$ps_file" ]]; then
    pass "place-source file written when switch on"
  else
    fail "place-source file missing when switch on"
  fi

  # Assemble staging like publish-packs (copy filter).
  stage="$TMP/stage/regions/${rid}"
  mkdir -p "$stage"
  find "$out" -maxdepth 1 -type f \( \
      -name '*.rkyv' -o -name '*.navi-manifest.json' -o -name '.convert-meta.json' \
      -o -name '*.navi-place-source.osm.pbf' \
    \) -exec cp -a {} "$stage"/ \;
  if [[ -f "${stage}/${rid}-latest.navi-place-source.osm.pbf" ]]; then
    pass "publish assemble copies place-source"
  else
    fail "publish assemble dropped place-source"
  fi

  # Client manifest files map would include the key (same logic as publish).
  python3 - "$stage" <<'PY'
import hashlib, json, sys
from pathlib import Path
src = Path(sys.argv[1])
files = {}
for path in sorted(src.iterdir()):
    if not path.is_file() or path.name.startswith("."):
        continue
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    files[path.name] = {"sha256": h, "bytes": path.stat().st_size}
key = [k for k in files if k.endswith(".navi-place-source.osm.pbf")]
assert key, files
assert files[key[0]]["bytes"] > 0
print("manifest_key", key[0], "bytes", files[key[0]]["bytes"])
PY
  pass "manifest entry shape includes place-source sha256/bytes"
else
  echo "SKIP: binary/fixture/osmium unavailable for on-switch test"
fi

# 3) Switch off again after a place-source file exists: script must not delete
# existing file when skipped? Spec: switch off means pipeline does not produce
# the file. build-place-source exits early without removing. Publish only copies
# what exists — operator cleans convert scratch. Documented behaviour: early exit.
export NAVI_BAKE_PLACE_SOURCE=0
unset NAVI_PLACE_SOURCE_BIN
before2="$(cd "$out" && find . -type f -printf '%P %s\n' | sort | sha256sum | awk '{print $1}')"
"${SCRIPT_DIR}/build-place-source.sh" --region "$rid"
after2="$(cd "$out" && find . -type f -printf '%P %s\n' | sort | sha256sum | awk '{print $1}')"
if [[ "$before2" == "$after2" ]]; then
  pass "switch off is a no-op (does not mutate convert out)"
else
  fail "switch off mutated convert out"
fi

echo "---"
echo "SUMMARY PASS=${PASS} FAIL=${FAIL}"
[[ "$FAIL" -eq 0 ]]
