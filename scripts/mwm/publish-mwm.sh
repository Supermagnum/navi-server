#!/usr/bin/env bash
# Atomically publish one MWM release under published/mwm/{version}/.
# current.json is written last. Never mixes leaves from different generator runs.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

VERSION_DIR=""
PUBLISH_ROOT="${NAVI_MWM_PUBLISHED_ROOT}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version-dir) VERSION_DIR="$2"; shift 2 ;;
    --publish-root) PUBLISH_ROOT="$2"; shift 2 ;;
    *) mwm_die "unknown arg: $1" ;;
  esac
done
[[ -n "$VERSION_DIR" && -d "$VERSION_DIR" ]] || mwm_die "need --version-dir"
mkdir -p "$PUBLISH_ROOT"

TAG="$(mwm_pin_field tag)"
SHA="$(mwm_pin_field sha)"
VER_NAME="$(basename "$VERSION_DIR")"
STAGING="${PUBLISH_ROOT}/.staging/${VER_NAME}"
FINAL="${PUBLISH_ROOT}/${VER_NAME}"

rm -rf "$STAGING"
mkdir -p "$STAGING"

python3 - "$VERSION_DIR" "$STAGING" "$NAVI_MWM_LEAFSET" "$TAG" "$SHA" \
  "$NAVI_MWM_REGION_MAP" <<'PY'
import hashlib, json, shutil, sys
from pathlib import Path

version = Path(sys.argv[1])
staging = Path(sys.argv[2])
leafset = Path(sys.argv[3])
tag, sha = sys.argv[4], sys.argv[5]
region_map = Path(sys.argv[6])
leaves = [ln.strip() for ln in leafset.read_text().splitlines() if ln.strip() and not ln.strip().startswith("#")]

def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

leaf_entries = []
checksums = {}
for leaf in leaves:
    mwm = version / f"{leaf}.mwm"
    sc = version / f"{leaf}.navi-sidecar.v1.zst"
    if not mwm.is_file() or not sc.is_file():
        raise SystemExit(f"missing artifacts for {leaf}")
    dst_m = staging / f"{leaf}.mwm"
    dst_s = staging / f"{leaf}.navi-sidecar.v1.zst"
    shutil.copy2(mwm, dst_m)
    shutil.copy2(sc, dst_s)
    ms = sha256(dst_m)
    ss = sha256(dst_s)
    checksums[f"{leaf}.mwm"] = ms
    checksums[f"{leaf}.navi-sidecar.v1.zst"] = ss
    leaf_entries.append({
        "id": leaf,
        "mwm_bytes": dst_m.stat().st_size,
        "mwm_sha256": ms,
        "sidecar_sha256": ss,
        "sidecar_bytes": dst_s.stat().st_size,
        "shared": False,
    })

attr_src = version / "attribution.json"
if attr_src.is_file():
    shutil.copy2(attr_src, staging / "attribution.json")
else:
    raise SystemExit("attribution.json required")

mapping = json.loads(region_map.read_text())
shared = mapping.get("shared_leaves") or {}
for e in leaf_entries:
    if e["id"] in shared:
        e["shared"] = True
        e["shared_with_regions"] = shared[e["id"]]

(staging / "checksums.sha256").write_text(
    "".join(f"{h}  {name}\n" for name, h in sorted(checksums.items()))
)

catalog = {
    "schema": "navi-mwm-catalog/v1",
    "version": staging.name if False else None,
    "generator": {"tag": tag, "sha": sha},
    "limitation": mapping.get("limitation")
        or "Cross-border routing out of the Nordic leaf set is not supported in the pilot.",
    "leaves": leaf_entries,
    "region_to_leaves": {
        rid: [c["leaf"] for c in (meta.get("comaps_leaves") or [])]
        for rid, meta in (mapping.get("navi_region_to_comaps_leaves") or {}).items()
    },
    "shared_leaves": shared,
}
# version filled by shell after we know VER_NAME
(staging / "catalog.body.json").write_text(json.dumps(catalog, indent=2) + "\n")
print("staged", len(leaf_entries), "leaves")
PY

# Fill version id and write catalog without promoting current yet
python3 - "$STAGING" "$VER_NAME" <<'PY'
import json, sys
from pathlib import Path
staging = Path(sys.argv[1])
ver = sys.argv[2]
body = json.loads((staging / "catalog.body.json").read_text())
body["version"] = ver
(staging / "catalog.json").write_text(json.dumps(body, indent=2) + "\n")
(staging / "catalog.body.json").unlink()
PY

# Atomic promote of release directory
rm -rf "$FINAL"
mv "$STAGING" "$FINAL"

# Retention before flipping current: keep current + previous grace
"${SCRIPT_DIR}/retain-mwm.sh" --publish-root "$PUBLISH_ROOT" --protect "$VER_NAME"

# Write current.json LAST (atomic replace)
TMP_CURRENT="${PUBLISH_ROOT}/current.json.tmp.$$"
python3 - "$FINAL/catalog.json" "$TMP_CURRENT" <<'PY'
import json, sys
from pathlib import Path
cat = json.loads(Path(sys.argv[1]).read_text())
# current.json is the catalog pointer + same body
Path(sys.argv[2]).write_text(json.dumps(cat, indent=2) + "\n")
PY
mv -f "$TMP_CURRENT" "${PUBLISH_ROOT}/current.json"
mwm_log "published ${FINAL}; current.json -> ${VER_NAME}"
