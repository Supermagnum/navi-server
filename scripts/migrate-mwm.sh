#!/usr/bin/env bash
# Migrate ONLY the published MWM tree (and optional caches/config).
# Dry-run by default; pass --apply to copy. Holds pack-pipeline.lock on source.
#
# Allow-list: data/published/mwm/**, config/mwm/**
# Optional: --with-mapterhorn-cache, --with-geometry-cache, --with-secrets
# Never: .rkyv packs, generations, published/current.json, scratch, logs, locks.
#
# Usage:
#   ./migrate-mwm.sh --source /media/navi/navi-server --dest /path/to/target
#   ./migrate-mwm.sh --source … --dest … --apply
#   ./migrate-mwm.sh --source … --dest user@host:/path --apply

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}/lib${PYTHONPATH:+:$PYTHONPATH}"

SOURCE=""
DEST=""
APPLY=0
WITH_GEOM=0
WITH_MH=0
WITH_SECRETS=0
WITH_CONFIG=1
GEOM_PATH=""
MH_PATH=""
LOCK_PATH=""

usage() { sed -n '2,14p' "$0"; exit "${1:-0}"; }
die() { echo "FATAL: $*" >&2; exit 1; }
log() { echo "[migrate-mwm] $*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    --apply) APPLY=1; shift ;;
    --dry-run) APPLY=0; shift ;;
    --with-geometry-cache) WITH_GEOM=1; shift ;;
    --with-mapterhorn-cache) WITH_MH=1; shift ;;
    --with-secrets) WITH_SECRETS=1; shift ;;
    --no-config) WITH_CONFIG=0; shift ;;
    --geometry-cache) GEOM_PATH="$2"; shift 2 ;;
    --mapterhorn-cache) MH_PATH="$2"; shift 2 ;;
    --lock) LOCK_PATH="$2"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) die "unknown arg: $1" ;;
  esac
done

[[ -n "$SOURCE" ]] || die "required: --source <navi-server-root>"
[[ -n "$DEST" ]] || die "required: --dest <local-dir|user@host:path>"
SOURCE="$(cd "$SOURCE" && pwd)"
[[ -d "$SOURCE/data/published/mwm" ]] || die "missing $SOURCE/data/published/mwm"
[[ -f "$SOURCE/data/published/mwm/.navi-mwm-owned" ]] \
  || die "missing .navi-mwm-owned under published/mwm"

LOCK_PATH="${LOCK_PATH:-$SOURCE/data/state/pack-pipeline.lock}"
if [[ ! -e "$LOCK_PATH" ]]; then
  if mkdir -p "$(dirname "$LOCK_PATH")" 2>/dev/null && : >>"$LOCK_PATH" 2>/dev/null; then
    :
  else
    LOCK_PATH="$(mktemp /tmp/migrate-mwm-lock.XXXXXX)"
    log "WARN: cannot create source pack-pipeline.lock — using ${LOCK_PATH}"
  fi
elif [[ ! -w "$LOCK_PATH" ]]; then
  # Still attempt flock (shared tree); if that fails python/flock will error.
  log "NOTE: ${LOCK_PATH} not writable by $(id -un); flock may still work"
fi

export MIGRATE_SOURCE="$SOURCE"
export MIGRATE_DEST="$DEST"
export MIGRATE_APPLY="$APPLY"
export MIGRATE_WITH_GEOM="$WITH_GEOM"
export MIGRATE_WITH_MH="$WITH_MH"
export MIGRATE_WITH_SECRETS="$WITH_SECRETS"
export MIGRATE_WITH_CONFIG="$WITH_CONFIG"
export MIGRATE_GEOM_PATH="$GEOM_PATH"
export MIGRATE_MH_PATH="$MH_PATH"

flock "$LOCK_PATH" python3 - <<'PY'
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from migrate_mwm import (
    MigrateMwmError,
    attach_checksums_from_release,
    build_plan,
    count_forbidden_in_plan,
    plan_summary_json,
    verify_copied_file,
)

src = Path(os.environ["MIGRATE_SOURCE"])
dest_spec = os.environ["MIGRATE_DEST"]
apply = os.environ.get("MIGRATE_APPLY", "0") == "1"
kwargs = {
    "with_geometry_cache": os.environ.get("MIGRATE_WITH_GEOM") == "1",
    "with_mapterhorn_cache": os.environ.get("MIGRATE_WITH_MH") == "1",
    "with_secrets": os.environ.get("MIGRATE_WITH_SECRETS") == "1",
    "with_config": os.environ.get("MIGRATE_WITH_CONFIG", "1") == "1",
}
if os.environ.get("MIGRATE_GEOM_PATH"):
    kwargs["geometry_cache"] = Path(os.environ["MIGRATE_GEOM_PATH"])
if os.environ.get("MIGRATE_MH_PATH"):
    kwargs["mapterhorn_cache"] = Path(os.environ["MIGRATE_MH_PATH"])

try:
    plan = build_plan(src, **kwargs)
except MigrateMwmError as e:
    print(f"FATAL: {e}", file=sys.stderr)
    raise SystemExit(2)
if plan.errors:
    print("FATAL:", "; ".join(plan.errors), file=sys.stderr)
    raise SystemExit(2)

attach_checksums_from_release(plan, src)
forbidden = count_forbidden_in_plan(plan)
print(plan_summary_json(plan))
print(f"forbidden_path_count={forbidden}")
if forbidden != 0:
    raise SystemExit("plan contains forbidden paths")

print(f"[migrate-mwm] files={len(plan.files)} bytes={plan.total_bytes}")
for row in plan.files[:15]:
    print(f"  {row.size}\t{row.rel}")
if len(plan.files) > 15:
    print(f"  … ({len(plan.files) - 15} more)")
    for row in plan.files[-3:]:
        print(f"  {row.size}\t{row.rel}")

if not apply:
    print("[migrate-mwm] dry-run only (pass --apply to copy)")
    raise SystemExit(0)

# Parse destination
mode = "local"
dest_host = ""
dest_root = dest_spec
if ":" in dest_spec and not dest_spec.startswith("/"):
    host, _, path = dest_spec.partition(":")
    if path.startswith("/"):
        mode = "ssh"
        dest_host, dest_root = host, path

staging = Path(tempfile.mkdtemp(prefix="migrate-mwm-"))
try:
    for f in plan.files:
        out = staging / f.rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f.src, out)

    if mode == "ssh":
        rsync = os.environ.get("MIGRATE_RSYNC", "rsync")
        ssh = os.environ.get("MIGRATE_SSH_CMD", "ssh")
        subprocess.check_call([ssh, "-o", "BatchMode=yes", dest_host, f"mkdir -p {dest_root}"])
        if (staging / "data").is_dir():
            subprocess.check_call(
                [rsync, "-a", "-e", ssh, str(staging / "data"), f"{dest_host}:{dest_root}/"]
            )
        if (staging / "config").is_dir():
            subprocess.check_call(
                [rsync, "-a", "-e", ssh, str(staging / "config"), f"{dest_host}:{dest_root}/"]
            )
        print(json.dumps({"ok": True, "mode": "ssh", "files": len(plan.files), "bytes": plan.total_bytes}))
        raise SystemExit(0)

    dest = Path(dest_root)
    dest.mkdir(parents=True, exist_ok=True)
    if (staging / "data").is_dir():
        shutil.copytree(staging / "data", dest / "data", dirs_exist_ok=True)
    if (staging / "config").is_dir():
        shutil.copytree(staging / "config", dest / "config", dirs_exist_ok=True)
    if (staging / "secrets").is_dir():
        out_sec = dest / "data/secrets-migrated-mwm"
        out_sec.mkdir(parents=True, exist_ok=True)
        for p in (staging / "secrets").rglob("*"):
            if p.is_file():
                t = out_sec / p.relative_to(staging / "secrets")
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, t)

    errors: list[str] = []
    for f in plan.files:
        if f.rel.parts[0] == "secrets":
            p = dest / "data/secrets-migrated-mwm" / Path(*f.rel.parts[1:])
        else:
            p = dest / f.rel
        if not p.is_file():
            errors.append(f"missing {f.rel}")
            continue
        try:
            verify_copied_file(p, f.sha256 or "")
        except MigrateMwmError as e:
            errors.append(str(e))

    src_cur = src / "data/published/mwm/current.json"
    dst_cur = dest / "data/published/mwm/current.json"
    if not dst_cur.is_file() or src_cur.read_bytes() != dst_cur.read_bytes():
        errors.append("current.json mismatch vs source")

    pub = dest / "data/published"
    if pub.is_dir():
        for child in pub.iterdir():
            if child.name != "mwm":
                errors.append(f"unexpected published entry: {child.name}")
    if (dest / "data/published/packs").exists():
        errors.append("packs/ present on target")

    if errors:
        print("VERIFY FAIL:", file=sys.stderr)
        for e in errors[:40]:
            print(" ", e, file=sys.stderr)
        raise SystemExit(1)

    print(json.dumps({"ok": True, "files": len(plan.files), "bytes": plan.total_bytes}))
finally:
    shutil.rmtree(staging, ignore_errors=True)
PY
