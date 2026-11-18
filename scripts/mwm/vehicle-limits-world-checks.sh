#!/usr/bin/env bash
# Post-world-bake checks for vehicle_limits. Scratch only.
#
# Live-publish gate (after human decision): every routable leaf has
# vehicle_limits coverage recorded (report present with map_release).
# Country ≥0.90 and Norway ≥0.95 remain informational / soft gates.
#
# Usage:
#   NAVI_VL_WORLD_WORK=/media/navi/haaken-scratch/mwm-maps-build/comaps-release-vl-world/261105 \
#     bash scripts/mwm/vehicle-limits-world-checks.sh
set -euo pipefail

REPO="${NAVI_SERVER_REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
WORK="${NAVI_VL_WORLD_WORK:-/media/navi/haaken-scratch/mwm-maps-build/comaps-release-vl-world/261105}"
META="${WORK}/meta/vehicle_limits"
SID="${WORK}/sidecars"
BIN="${REPO}/target/release/navi-mwm-sidecar"
PYTHON="${NAVI_MWM_PYTHON:-python3}"
NORWAY_MIN="${NAVI_VL_NORWAY_MIN:-0.95}"
COUNTRY_MIN="${NAVI_VL_COUNTRY_MIN:-0.90}"
OUT="${NAVI_VL_GATE_OUT:-/tmp/navi-vl-world-gate.json}"

[[ -d "$META" ]] || { echo "FATAL: missing $META" >&2; exit 1; }

"$PYTHON" - <<PY
import json, subprocess, sys
from collections import defaultdict
from pathlib import Path

meta = Path("${META}")
sid = Path("${SID}")
bin_path = Path("${BIN}")
norway_min = float("${NORWAY_MIN}")
country_min = float("${COUNTRY_MIN}")
out_path = Path("${OUT}")

by_country = defaultdict(lambda: {"matched": 0, "parseable": 0, "records": 0, "leaves": []})
norway = {"matched": 0, "parseable": 0, "records": 0, "leaves": []}
guards = defaultdict(int)
coverage_missing = []
coverage_ok = 0

for p in sorted(meta.glob("*.report.json")):
    r = json.loads(p.read_text())
    leaf = p.name.replace(".report.json", "")
    nn = int(r.get("non_numeric_no_limit") or 0)
    unk = int(r.get("unknown_value") or 0)
    if "numeric_ways" in r:
        parseable = int(r["numeric_ways"])
    else:
        parseable = int(r["tagged_ways"]) - int(r.get("skipped_unparseable") or 0)
    matched = int(r["matched_ways"])
    recs = int(r.get("records", 0))
    rate = float(r.get("match_rate") or (matched / parseable if parseable else 0.0))
    map_rel = str(r.get("map_release") or "").strip()
    if map_rel and "tagged_ways" in r and "matched_ways" in r and "match_rate" in r:
        coverage_ok += 1
    else:
        coverage_missing.append(leaf)
    country = leaf.split("_", 1)[0]
    by_country[country]["matched"] += matched
    by_country[country]["parseable"] += parseable
    by_country[country]["records"] += recs
    by_country[country]["leaves"].append(
        {"leaf": leaf, "matched": matched, "parseable": parseable, "rate": rate, "records": recs}
    )
    for k, v in (r.get("guard_rejections") or {}).items():
        guards[k] += int(v)
    if country == "Norway":
        norway["matched"] += matched
        norway["parseable"] += parseable
        norway["records"] += recs
        norway["leaves"].append({"leaf": leaf, "rate": rate, "matched": matched, "parseable": parseable})

countries = {}
soft_fail_countries = []
for c, d in sorted(by_country.items()):
    rate = d["matched"] / d["parseable"] if d["parseable"] else 0.0
    countries[c] = {
        **{k: d[k] for k in ("matched", "parseable", "records")},
        "rate": rate,
        "n_leaves": len(d["leaves"]),
    }
    print(
        f"country {c:20s} {d['matched']:6d}/{d['parseable']:6d} "
        f"rate={rate:.4f} leaves={len(d['leaves'])} recs={d['records']}"
    )
    if d["parseable"] >= 100 and rate < country_min:
        soft_fail_countries.append(c)

norway_rate = norway["matched"] / norway["parseable"] if norway["parseable"] else 0.0
print(f"NORWAY {norway['matched']}/{norway['parseable']} rate={norway_rate:.4f}")
print("GUARDS", dict(guards))
print(f"coverage_recorded {coverage_ok} missing={len(coverage_missing)}")

# Sidecar spot-check when binary exists
fp_ok = 0
fp_bad = []
if bin_path.is_file() and sid.is_dir():
    sample = sorted(sid.glob("Norway_*.navi-sidecar.v1.zst"))[:24]
    for sc in sample:
        try:
            raw = subprocess.check_output(
                [str(bin_path), "inspect", "--sidecar", str(sc)],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            j = json.loads(raw)
            if j.get("vehicle_limits_records") is None and "vehicle_limits" not in str(j):
                # section may be listed under sections
                names = [s.get("name") for s in (j.get("sections") or [])]
                if "vehicle_limits" not in names:
                    fp_bad.append(sc.name)
                    continue
            fp_ok += 1
        except Exception as exc:
            fp_bad.append(f"{sc.name}:{exc}")
    print(f"inspect_ok {fp_ok}/{fp_ok + len(fp_bad)} bad={fp_bad[:5]}")
else:
    print("inspect_ok skipped (no binary or sidecars dir)")

reasons = []
if coverage_missing:
    reasons.append(f"coverage_missing:{len(coverage_missing)}")
# Soft quality signals (do not block coverage-recorded gate)
soft = []
if norway["parseable"] and norway_rate < norway_min:
    soft.append(f"norway_below_{norway_min}:{norway_rate:.4f}")
if soft_fail_countries:
    soft.append("countries_below_0.9: " + str(soft_fail_countries))

decision = "GO" if not coverage_missing else "STOP"
print(f"DECISION {decision} hard={reasons} soft={soft}")
gate = {
    "decision": decision,
    "gate": "coverage_recorded_for_every_leaf",
    "coverage_ok": coverage_ok,
    "coverage_missing": coverage_missing[:50],
    "coverage_missing_count": len(coverage_missing),
    "norway_rate": norway_rate,
    "soft_countries_below_min": soft_fail_countries,
    "soft": soft,
    "guards": dict(guards),
}
out_path.write_text(json.dumps(gate, indent=2) + "\n")
print("wrote", out_path)
sys.exit(0 if decision == "GO" else 2)
PY
