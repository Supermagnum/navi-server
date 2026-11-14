#!/usr/bin/env bash
# Whole-release retention for published/mwm.
# Keep the current release plus the previous one for NAVI_MWM_GRACE_DAYS
# after it is superseded. Never delete a release referenced by current.json.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

PUBLISH_ROOT="${NAVI_MWM_PUBLISHED_ROOT}"
PROTECT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --publish-root) PUBLISH_ROOT="$2"; shift 2 ;;
    --protect) PROTECT="$2"; shift 2 ;;
    *) mwm_die "unknown arg: $1" ;;
  esac
done

python3 - "$PUBLISH_ROOT" "${NAVI_MWM_GRACE_DAYS}" "${NAVI_MWM_KEEP_RELEASES}" "$PROTECT" "${SCRIPT_DIR}/../lib" <<'PY'
import json, os, shutil, sys, time
from pathlib import Path

sys.path.insert(0, sys.argv[5])
from rkyv_published_guard import ensure_mwm_owned_marker, MWM_OWNED_MARKER

root = Path(sys.argv[1])
grace_days = int(sys.argv[2])
keep = max(2, int(sys.argv[3]))
protect = sys.argv[4] or None
root.mkdir(parents=True, exist_ok=True)
ensure_mwm_owned_marker(root)

current_ver = None
cur = root / "current.json"
if cur.is_file():
    try:
        current_ver = json.loads(cur.read_text()).get("version")
    except json.JSONDecodeError:
        current_ver = None

releases = []
for p in root.iterdir():
    if not p.is_dir():
        continue
    if p.name.startswith("."):
        continue
    if p.name == MWM_OWNED_MARKER:
        continue
    releases.append(p)

# Sort by mtime descending (newest first)
releases.sort(key=lambda p: p.stat().st_mtime, reverse=True)

protected = set()
if current_ver:
    protected.add(current_ver)
if protect:
    protected.add(protect)

# Always keep newest `keep` releases among existing+protect
for p in releases[:keep]:
    protected.add(p.name)

now = time.time()
grace_s = grace_days * 86400
# Previous (second newest) gets grace from when it was superseded —
# approximate: if older than grace and not in protected keep set beyond index 1, delete.
for idx, p in enumerate(releases):
    if p.name in protected:
        continue
    age = now - p.stat().st_mtime
    # Keep second slot through grace window
    if idx == 1 and age < grace_s:
        continue
    if idx < keep:
        continue
    print(f"retain: delete superseded release {p}")
    shutil.rmtree(p)

# Never delete current.json target
if current_ver and not (root / current_ver).is_dir():
    print(f"WARNING: current.json references missing {current_ver}", file=sys.stderr)

print(f"retain OK protected={sorted(protected)}")
PY
