#!/usr/bin/env bash
# Promote scratch 261105 sidecars (with vehicle_limits) to the LIVE published tree.
# YOU run this after verifying scratch. No sudo inside; if the live root needs
# elevated write, wrap the rsync yourself.
#
# Gate: coverage recorded for every leaf in the scratch catalog (not country ≥0.90).
# This script does NOT execute the promote. It prints the commands to review.
set -euo pipefail

SCRATCH="${NAVI_MWM_SCRATCH_PUBLISH:-/media/navi/haaken-scratch/mwm-published-vehicle-limits}"
LIVE="${NAVI_MWM_LIVE_PUBLISH:-/media/navi/navi-server/data/published/mwm}"
VER="${NAVI_MWM_COMAPS_VERSION:-261105}"
PYTHON="${NAVI_MWM_PYTHON:-python3}"

SRC="$SCRATCH/$VER"
DST="$LIVE/$VER"
CATALOG="$SRC/catalog.json"
[[ -f "$CATALOG" ]] || CATALOG="$SCRATCH/catalog.json"

[[ -d "$SRC" ]] || { echo "missing scratch version dir: $SRC" >&2; exit 1; }
[[ -f "$CATALOG" ]] || {
  echo "FATAL: need catalog.json under scratch to verify coverage gate" >&2
  exit 1
}

"$PYTHON" - "$CATALOG" <<'PY'
import json, sys
from pathlib import Path
cat = json.loads(Path(sys.argv[1]).read_text())
missing = []
for leaf in cat.get("leaves") or []:
    if leaf.get("no_sidecar"):
        continue
    cov = leaf.get("vehicle_limits_coverage")
    if not isinstance(cov, dict):
        missing.append(leaf.get("id"))
        continue
    if "tagged_ways" not in cov or "matched_ways" not in cov or "match_rate" not in cov:
        missing.append(leaf.get("id"))
        continue
    if not str(cov.get("map_release") or "").strip():
        missing.append(leaf.get("id"))
if missing:
    print(f"GATE FAIL: coverage missing for {len(missing)} leaves (e.g. {missing[:10]})", file=sys.stderr)
    sys.exit(2)
print(f"GATE OK: coverage recorded for all {(len(cat.get('leaves') or []))} catalog leaves (no_sidecar skipped)")
PY

echo "DRY-RUN first. Source: $SRC"
echo "Destination: $DST"
echo
echo "Coverage gate passed. Commands to run when YOU decide to go live:"
cat <<EOF
mkdir -p "$LIVE"
# Backup live version tree
if [[ -d "$DST" ]]; then
  cp -a "$DST" "${DST}.bak-\$(date -u +%Y%m%dT%H%M%SZ)"
fi
rsync -a --delete "$SRC/" "$DST/"
# Point current.json at this version (adjust path if your layout differs)
python3 - <<'PY'
import json
from pathlib import Path
live = Path("$LIVE")
ver = "$VER"
cur = {"version": ver, "path": ver}
(live / "current.json").write_text(json.dumps(cur, indent=2) + "\\n")
print("wrote", live / "current.json", cur)
PY
EOF

echo
echo "This script does NOT execute the promote. Copy/paste the block above after review."
