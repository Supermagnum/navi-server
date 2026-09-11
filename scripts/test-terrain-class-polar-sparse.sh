#!/usr/bin/env bash
# Unit checks for terrain_class band merge (polar_sparse + wetland_heavy +
# dense_network, including multi-class lines). Does not touch the live planet bake.
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

python3 - "$TMP" <<'PY'
import os, sys
from pathlib import Path

tmp = Path(sys.argv[1])
polar = float(os.environ.get("NAVI_SIZE_GRAPH_MIN_RATIO_POLAR_SPARSE", "0.001"))
dense_hi = float(os.environ.get("NAVI_SIZE_GRAPH_MAX_RATIO_DENSE_NETWORK", "28.0"))
wet_hi = float(os.environ.get("NAVI_SIZE_WETLAND_MAX_RATIO_WETLAND_HEAVY", "1.0"))
global_min = float(os.environ.get("NAVI_SIZE_GRAPH_MIN_RATIO", "0.10"))
global_max = float(os.environ.get("NAVI_SIZE_GRAPH_MAX_RATIO", "20.0"))
global_wet_max = float(os.environ.get("NAVI_SIZE_WETLAND_MAX_RATIO", "0.50"))

weekly = tmp / "regions.conf"
planet = tmp / "regions.planet.conf"
weekly.write_text(
    "antarctica\tgeofabrik:antarctica\tterrain_class=polar_sparse\n"
    "africa_guinea_bissau\tgeofabrik:africa/guinea-bissau\tterrain_class=wetland_heavy\n"
    "north_america_us_florida\tgeofabrik:north-america/us/florida\tterrain_class=wetland_heavy\n"
    "asia_thailand\tgeofabrik:asia/thailand\tterrain_class=dense_network\n"
    "asia_vietnam\tgeofabrik:asia/vietnam\tterrain_class=wetland_heavy,dense_network\n"
    "tagged_plus_numeric\tgeofabrik:x\tterrain_class=polar_sparse\tgraph_min_ratio=0.05\n"
    "wet_plus_numeric\tgeofabrik:y\tterrain_class=wetland_heavy\twetland_max_ratio=0.9\n"
    "dense_plus_numeric\tgeofabrik:z\tterrain_class=dense_network\tgraph_max_ratio=22.0\n"
)
planet.write_text(
    "antarctica\tgeofabrik:antarctica\n"
    "north_america_us_ohio\tgeofabrik:north-america/us/ohio\n"
    "africa_guinea_bissau\tgeofabrik:africa/guinea-bissau\n"
    "north_america_us_florida\tgeofabrik:north-america/us/florida\n"
    "asia_thailand\tgeofabrik:asia/thailand\n"
    "asia_vietnam\tgeofabrik:asia/vietnam\n"
    "tagged_plus_numeric\tgeofabrik:x\n"
    "wet_plus_numeric\tgeofabrik:y\n"
    "dense_plus_numeric\tgeofabrik:z\n"
)

_OVERRIDE_KEYS = {
    "graph_min_ratio": ("graph", 0),
    "graph_max_ratio": ("graph", 1),
    "wetland_max_ratio": ("wetland", 1),
}
_TERRAIN_G_MIN = {"polar_sparse": polar}
_TERRAIN_G_MAX = {"dense_network": dense_hi}
_TERRAIN_W = {"wetland_heavy": wet_hi}
default_bands = {
    "graph": (global_min, global_max),
    "wetland": (0.0, global_wet_max),
}

def parse_classes(raw: str):
    out = []
    for tok in raw.replace("+", ",").split(","):
        c = tok.strip().lower()
        if c and c not in out:
            out.append(c)
    return out

def merge_terrain(dst, rid, classes):
    cur = dst.get(rid) or []
    if isinstance(cur, str):
        cur = [cur]
    for c in classes:
        if c not in cur:
            cur.append(c)
    dst[rid] = cur

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
        overrides, classes = {}, []
        for tok in parts[2:]:
            if "=" not in tok:
                continue
            k, v = tok.split("=", 1)
            k, v = k.strip().lower(), v.strip()
            if k == "terrain_class":
                classes.extend(parse_classes(v))
                continue
            if k in _OVERRIDE_KEYS:
                overrides[k] = float(v)
        if overrides:
            overrides_out[rid] = overrides
        if classes:
            merge_terrain(terrain_out, rid, classes)
    return overrides_out, terrain_out

region_overrides, region_terrain = load(planet)
ov2, tc2 = load(weekly)
for rid, vals in ov2.items():
    region_overrides.setdefault(rid, {}).update(vals)
for rid, classes in tc2.items():
    merge_terrain(region_terrain, rid, classes if isinstance(classes, list) else [classes])

def bands_for(rid):
    bands = {k: v for k, v in default_bands.items()}
    notes = {}
    terrains = region_terrain.get(rid) or []
    if isinstance(terrains, str):
        terrains = [terrains]
    for terrain in terrains:
        if terrain in _TERRAIN_G_MIN:
            lo, hi = bands["graph"]
            bands["graph"] = (_TERRAIN_G_MIN[terrain], hi)
            notes["graph"] = f" (terrain_class={terrain})"
        if terrain in _TERRAIN_G_MAX:
            lo, hi = bands["graph"]
            bands["graph"] = (lo, _TERRAIN_G_MAX[terrain])
            notes["graph"] = f" (terrain_class={terrain})"
        if terrain in _TERRAIN_W:
            lo, hi = bands["wetland"]
            bands["wetland"] = (lo, _TERRAIN_W[terrain])
            notes["wetland"] = f" (terrain_class={terrain})"
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
checks.append(("antarctica wetland untouched", b["wetland"][1] == global_wet_max, b, n))
b, n = bands_for("north_america_us_ohio")
checks.append(("ohio untouched graph_min", b["graph"][0] == global_min, b, n))
checks.append(("ohio untouched graph_max", b["graph"][1] == global_max, b, n))
checks.append(("ohio untouched wetland", b["wetland"][1] == global_wet_max, b, n))
checks.append(("ohio no note", not n, n))
b, n = bands_for("africa_guinea_bissau")
checks.append(("gw wetland max", b["wetland"][1] == wet_hi, b, n))
checks.append(("gw wetland note", "terrain_class=wetland_heavy" in n.get("wetland", ""), n))
checks.append(("gw graph still global", b["graph"][0] == global_min and b["graph"][1] == global_max, b, n))
b, n = bands_for("north_america_us_florida")
checks.append(("florida wetland max", b["wetland"][1] == wet_hi, b, n))
b, n = bands_for("asia_thailand")
checks.append(("thailand graph_max", b["graph"][1] == dense_hi, b, n))
checks.append(("thailand graph_min global", b["graph"][0] == global_min, b, n))
checks.append(("thailand note", "terrain_class=dense_network" in n.get("graph", ""), n))
checks.append(("thailand wetland untouched", b["wetland"][1] == global_wet_max, b, n))
b, n = bands_for("asia_vietnam")
checks.append(("vietnam multi wetland", b["wetland"][1] == wet_hi, b, n))
checks.append(("vietnam multi graph_max", b["graph"][1] == dense_hi, b, n))
checks.append(("vietnam wetland note", "terrain_class=wetland_heavy" in n.get("wetland", ""), n))
checks.append(("vietnam graph note", "terrain_class=dense_network" in n.get("graph", ""), n))
b, n = bands_for("tagged_plus_numeric")
checks.append(("explicit graph wins", b["graph"][0] == 0.05, b, n))
checks.append(("explicit graph note", n.get("graph") == " (region override)", n))
b, n = bands_for("wet_plus_numeric")
checks.append(("explicit wetland wins", b["wetland"][1] == 0.9, b, n))
checks.append(("explicit wetland note", n.get("wetland") == " (region override)", n))
b, n = bands_for("dense_plus_numeric")
checks.append(("explicit dense max wins", b["graph"][1] == 22.0, b, n))
checks.append(("explicit dense note", n.get("graph") == " (region override)", n))

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
