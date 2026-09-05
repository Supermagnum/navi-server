#!/usr/bin/env bash
# One-shot: move legacy flat published packs/<bake_id>/<gen>/ into
# packs/<geofabrik-path>/<gen>/ and rebuild current.json.
#
# Usage:
#   ./migrate-published-to-geofabrik-paths.sh           # apply
#   ./migrate-published-to-geofabrik-paths.sh --dry-run

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

PACKS="${NAVI_PUBLISHED_DIR}/packs"
[[ -d "$PACKS" ]] || die "missing ${PACKS}"

python3 - "$PACKS" "$NAVI_PACK_ROOT" "$DRY_RUN" "${SCRIPT_DIR}/lib" <<'PY'
import json, os, shutil, sys
from pathlib import Path

sys.path.insert(0, sys.argv[4])
from published_tree import (
    is_generation_dir,
    load_region_sources,
    publish_relpath,
    rebuild_current_json,
)

packs = Path(sys.argv[1])
pack_root = Path(sys.argv[2])
dry = sys.argv[3] == "1"

sources = load_region_sources(
    pack_root / "regions.planet.conf",
    pack_root / "regions.conf",
)

moved = 0
skipped = 0
for child in sorted(packs.iterdir()):
    if not child.is_dir() or child.name.startswith("."):
        continue
    # Legacy flat = bake_id directory whose children are generation dirs.
    gens = [d for d in child.iterdir() if d.is_dir() and is_generation_dir(d.name)]
    if not gens:
        # Already nested (e.g. asia/) or empty — leave alone.
        skipped += 1
        continue
    bake_id = child.name
    rel = publish_relpath(bake_id, sources)
    if rel == bake_id:
        print(f"keep flat (no geofabrik mapping): {bake_id}")
        skipped += 1
        continue
    dest_root = packs / rel
    print(f"migrate {bake_id} -> {rel} gens={len(gens)}")
    if dry:
        moved += 1
        continue
    dest_root.mkdir(parents=True, exist_ok=True)
    for g in gens:
        dest = dest_root / g.name
        if dest.exists():
            raise SystemExit(f"refusing to overwrite existing {dest}")
        shutil.move(str(g), str(dest))
        # Patch manifest region_id / bake_id for clients.
        man_path = dest / "manifest.json"
        if man_path.is_file():
            man = json.loads(man_path.read_text(encoding="utf-8"))
            man["bake_id"] = bake_id
            man["region_id"] = rel
            man_path.write_text(json.dumps(man, indent=2) + "\n", encoding="utf-8")
    # Remove empty legacy dir
    try:
        child.rmdir()
    except OSError:
        # leftover non-gen files
        pass
    moved += 1

if dry:
    print(f"dry-run: would migrate {moved} regions (skipped={skipped})")
    raise SystemExit(0)

regions = rebuild_current_json(packs.parent, generation="migrate-geofabrik-paths")
print(f"migrated={moved} skipped={skipped} catalog_regions={len(regions)}")
print(f"wrote {packs.parent / 'current.json'}")
PY
