#!/usr/bin/env bash
# Promote scratch 261105 sidecars (with vehicle_limits) to the LIVE published tree.
# YOU run this after verifying scratch. No sudo inside; if the live root needs
# elevated write, wrap the rsync yourself.
#
# This script does NOT execute the promote. It prints the commands to review.
set -euo pipefail

SCRATCH="${NAVI_MWM_SCRATCH_PUBLISH:-/media/navi/haaken-scratch/mwm-published-vehicle-limits}"
LIVE="${NAVI_MWM_LIVE_PUBLISH:-/media/navi/navi-server/data/published/mwm}"
VER="${NAVI_MWM_COMAPS_VERSION:-261105}"

SRC="$SCRATCH/$VER"
DST="$LIVE/$VER"

[[ -d "$SRC" ]] || { echo "missing scratch version dir: $SRC" >&2; exit 1; }
[[ -f "$SRC/catalog.json" || -f "$SCRATCH/current.json" ]] || {
  echo "WARN: catalog/current.json not found under scratch; continuing" >&2
}

echo "DRY-RUN first. Source: $SRC"
echo "Destination: $DST"
echo
echo "Commands to run when ready:"
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
