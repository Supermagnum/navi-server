#!/usr/bin/env bash
# Unit checks for terrain_class=polar_sparse band merge behavior.
# Does not touch the live planet bake.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

PASS=0
FAIL=0
ok() { echo "PASS: $*"; PASS=$((PASS + 1)); }
bad() { echo "FAIL: $*"; FAIL=$((FAIL + 1)); }

TMP="$(mktemp -d "${TMPDIR:-/tmp}/navi-terrain-class.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

# Mirror the validate-packs.sh merge rules in a focused unit test.
python3 - "$TMP" <<'PY'
import os, sys
from pathlib import Path

tmp = Path(sys.argv[1])
polar = float(os.environ.get("NAVI_SIZE_GRAPH_MIN_RATIO_POLAR_SPARSE", "0.001"))
global_min = float(os.environ.get("NAVI_SIZE_GRAPH_MIN_RATIO", "0.10"))

weekly = tmp / "regions.conf"
planet = tmp / "regions.planet.conf"
weekly.write_text(
    "antarctica\tgeofabrik:antarctica\tterrain_class=polar_sparse\n"
    "africa_guinea_bissau\tgeofabrik:africa/guinea-bissau\twetland_max_ratio=1.0\n"
    "tagged_plus_numeric\tgeofabrik:x\tterrain_class=polar_sparse\tgraph_min_ratio=0.05\n"
)
planet.write_text(
    "antarctica\tgeofabrik:antarctica\n"
    "north_america_us_ohio\tgeofabrik:north-america/us/ohio\n"
    "africa_guinea_bissau\tgeofabrik:africa/guinea-bissau\n"
    "tagged_plus_numeric\tgeofabrik:x\n"
)

_OVERRIDE_KEYS = {
    "graph_min_ratio": ("graph", 0),
    "graph_max_ratio": ("graph", 1),
    "wetland_max_ratio": ("wetland", 1),
}
_TERRAIN = {"polar_sparse": polar}
default_bands = {
    "graph": (global_min, 20.0),
    "wetland": (0.0, 0.50),
}

def load(conf: Path):
    overrides_out, terrain_out = {}, {}
    for raw in conf.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        rid = parts[0]
        overrides, terrain = {}, None
        for tok in parts[2:]:
            if "=" not in tok:
                continue
            k, v = tok.split("=", 1)
            k, v = k.strip().lower(), v.strip()
            if k == "terrain_class":
                terrain = v.lower()
                continue
            if k in _OVERRIDE_KEYS:
                overrides[k] = float(v)
        if overrides:
            overrides_out[rid] = overrides
        if terrain:
            terrain_out[rid] = terrain
    return overrides_out, terrain_out

region_overrides, region_terrain = load(planet)
ov2, tc2 = load(weekly)
for rid, vals in ov2.items():
    region_overrides.setdefault(rid, {}).update(vals)
for rid, cls in tc2.items():
    region_terrain.setdefault(rid, cls)

def bands_for(rid):
    bands = {k: v for k, v in default_bands.items()}
    notes = {}
    terrain = region_terrain.get(rid)
    if terrain in _TERRAIN:
        lo, hi = bands["graph"]
        bands["graph"] = (_TERRAIN[terrain], hi)
        notes["graph"] = f" (terrain_class={terrain})"
    for key, val in region_overrides.get(rid, {}).items():
        kind, idx = _OVERRIDE_KEYS[key]
        lo, hi = bands[kind]
        bands[kind] = (val, hi) if idx == 0 else (lo, val)
        notes[kind] = " (region override)"
    return bands, notes

checks = []
b, n = bands_for("antarctica")
checks.append(("antarctica graph_min", b["graph"][0] == polar, b, n))
checks.append(("antarctica note", "terrain_class=polar_sparse" in n.get("graph", ""), n))
b, n = bands_for("north_america_us_ohio")
checks.append(("ohio untouched graph_min", b["graph"][0] == global_min, b, n))
checks.append(("ohio no note", "graph" not in n, n))
b, n = bands_for("africa_guinea_bissau")
checks.append(("gw wetland max", b["wetland"][1] == 1.0, b, n))
checks.append(("gw graph still global", b["graph"][0] == global_min, b, n))
b, n = bands_for("tagged_plus_numeric")
checks.append(("explicit wins", b["graph"][0] == 0.05, b, n))
checks.append(("explicit note", n.get("graph") == " (region override)", n))

failed = 0
for name, ok, *rest in checks:
    if ok:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} got={rest}")
        failed += 1
sys.exit(failed)
PY
rc=$?
if [[ $rc -eq 0 ]]; then
  ok "python terrain_class merge unit checks"
else
  bad "python terrain_class merge unit checks (rc=$rc)"
fi

echo "---"
echo "SUMMARY pass=${PASS} fail=${FAIL}"
[[ "$FAIL" -eq 0 ]]
