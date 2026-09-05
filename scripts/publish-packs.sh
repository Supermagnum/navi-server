#!/usr/bin/env bash
# Publish step: assemble staging generation, validate, atomically swap live.
# Keeps previous generation as rollback via NAVI_PREVIOUS_LINK.
#
# Usage:
#   ./publish-packs.sh                         # from convert scratch (--all regions)
#   ./publish-packs.sh --region hedmark        # single region into a new generation
#   ./publish-packs.sh --skip-validate         # emergency only
#   ./publish-packs.sh --dry-run

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config
require_cmd python3

SKIP_VALIDATE=0
DRY_RUN=0
FILTER_IDS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-validate) SKIP_VALIDATE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --region) FILTER_IDS+=("$2"); shift 2 ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *) FILTER_IDS+=("$1"); shift ;;
  esac
done

GEN_ID="$(generation_id)"
STAGE="${NAVI_STAGING_DIR}/${GEN_ID}"
FINAL="${NAVI_GENERATIONS_DIR}/${GEN_ID}"

log_info "publish generation=${GEN_ID}"

mkdir -p "${STAGE}/regions"

assemble_one() {
  local region_id="$1"
  local src="${NAVI_CONVERT_DIR}/${region_id}"
  local dst="${STAGE}/regions/${region_id}"
  [[ -d "$src" ]] || die "convert output missing for ${region_id}: ${src}"
  [[ -n "$(find "$src" -maxdepth 1 -name '*.navi-manifest.json' -print -quit)" ]] \
    || die "no manifest in ${src}"
  mkdir -p "$dst"
  # Copy packs + manifest; leave scratch convert tree intact for debugging.
  find "$src" -maxdepth 1 -type f \( \
      -name '*.rkyv' -o -name '*.navi-manifest.json' -o -name '.convert-meta.json' \
    \) -exec cp -a {} "$dst"/ \;
  log_info "staged region=${region_id} -> ${dst}"
}

matched=0
while IFS=$'\t' read -r region_id src; do
  if [[ ${#FILTER_IDS[@]} -gt 0 ]]; then
    keep=0
    for f in "${FILTER_IDS[@]}"; do
      [[ "$f" == "$region_id" ]] && keep=1
    done
    [[ "$keep" -eq 1 ]] || continue
  fi
  matched=1
  assemble_one "$region_id"
done < <(list_regions)

[[ "$matched" -eq 1 ]] || die "no matching regions to publish"

# Optional town-routes already baked into convert? Prefer explicit bake into STAGE.
if [[ "${NAVI_BAKE_TOWN_ROUTES}" == "1" ]]; then
  bake_args=(--generation-dir "$STAGE")
  if [[ ${#FILTER_IDS[@]} -gt 0 ]]; then
    bake_args+=("${FILTER_IDS[@]}")
  else
    bake_args+=(--all)
  fi
  "${SCRIPT_DIR}/bake-town-routes.sh" "${bake_args[@]}"
fi

# Write generation catalog (what a future client/CDN index would list).
python3 - "$STAGE" "$GEN_ID" <<'PY'
import json, os, sys, time
from pathlib import Path
stage, gen_id = sys.argv[1], sys.argv[2]
regions = []
root = Path(stage) / "regions"
for rd in sorted(p for p in root.iterdir() if p.is_dir()):
    mans = sorted(rd.glob("*.navi-manifest.json"))
    if not mans:
        continue
    man = json.loads(mans[0].read_text(encoding="utf-8"))
    files = [p.name for p in rd.iterdir() if p.is_file() and not p.name.startswith(".")]
    regions.append({
        "region_id": rd.name,
        "stem": man.get("stem"),
        "manifest": mans[0].name,
        "has_delta_h": bool(man.get("has_delta_h")),
        "files": sorted(files),
        "bytes": sum(p.stat().st_size for p in rd.iterdir() if p.is_file()),
    })
payload = {
    "schema": 1,
    "generation": gen_id,
    "created_unix": int(time.time()),
    "regions": regions,
}
out = Path(stage) / "generation-manifest.json"
out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out} regions={len(regions)}")
PY

if [[ "$SKIP_VALIDATE" -eq 0 ]]; then
  log_info "validating staging tree ${STAGE}"
  "${SCRIPT_DIR}/validate-packs.sh" "$STAGE"
else
  log_warn "SKIP VALIDATE — staging will still be published"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  log_info "dry-run: leaving staging at ${STAGE}; not swapping live"
  exit 0
fi

# Move staging -> generations (same filesystem → rename).
if [[ -e "$FINAL" ]]; then
  die "generation already exists: $FINAL"
fi
mv "$STAGE" "$FINAL"

# Atomic blue-green swap via symlink dance.
live_target=""
if [[ -L "$NAVI_LIVE_LINK" ]]; then
  live_target="$(readlink -f "$NAVI_LIVE_LINK")"
fi

tmp_live="${NAVI_LIVE_LINK}.new.$$"
ln -sfn "$FINAL" "$tmp_live"
mv -Tf "$tmp_live" "$NAVI_LIVE_LINK"

if [[ -n "$live_target" && -d "$live_target" && "$live_target" != "$FINAL" ]]; then
  tmp_prev="${NAVI_PREVIOUS_LINK}.new.$$"
  ln -sfn "$live_target" "$tmp_prev"
  mv -Tf "$tmp_prev" "$NAVI_PREVIOUS_LINK"
  log_info "previous -> ${live_target}"
fi

# Pointer file for humans / monitoring (internal — not under published/).
printf '%s\n' "$GEN_ID" >"${NAVI_PACK_ROOT}/CURRENT_GENERATION"

# Sync the immutable generation into the HTTP-facing static tree only.
# DocumentRoot for the public server is NAVI_PUBLISHED_DIR; nothing else is served.
#
# Per-region update semantics (critical):
# - never wipe packs/<geofabrik-path>/ or the whole published tree
# - write the new generation beside any last-known-good for that region
# - rebuild current.json from all complete packs/* so untouched regions survive
# - prune older gens only under packs/<path>/ after the new gen is confirmed
# - layout uses Geofabrik paths (asia/china/anhui) matching the Navi app picker
keep_prior="${NAVI_PUBLISHED_KEEP_PER_REGION:-1}"
log_info "syncing static published tree under ${NAVI_PUBLISHED_DIR} (keep_prior=${keep_prior})"

# Bake-id -> publish relpath map for this run (Geofabrik path when available).
PUBLISH_MAP="$(mktemp)"
trap 'rm -f "$PUBLISH_MAP"' EXIT
while IFS=$'\t' read -r region_id _src; do
  printf '%s\t%s\n' "$region_id" "$(region_publish_relpath "$region_id")"
done < <(list_regions) >"$PUBLISH_MAP"

python3 - "$FINAL" "$GEN_ID" "$NAVI_PUBLISHED_DIR" "$keep_prior" "$PUBLISH_MAP" \
    "${SCRIPT_DIR}/lib" <<'PY'
import hashlib, json, shutil, sys
from pathlib import Path

sys.path.insert(0, sys.argv[6])
from published_tree import complete_gens, rebuild_current_json

final = Path(sys.argv[1])
gen_id = sys.argv[2]
published = Path(sys.argv[3])
keep_prior = max(0, int(sys.argv[4]))
path_map = {}
for line in Path(sys.argv[5]).read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    bake_id, rel = line.split("\t", 1)
    path_map[bake_id] = rel

packs_root = published / "packs"
packs_root.mkdir(parents=True, exist_ok=True)

gen_man_path = final / "generation-manifest.json"
gen_man = json.loads(gen_man_path.read_text(encoding="utf-8"))

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

for region in gen_man.get("regions", []):
    bake_id = region["region_id"]
    relpath = path_map.get(bake_id, bake_id)
    src = final / "regions" / bake_id
    if not src.is_dir():
        raise SystemExit(f"missing region dir in generation: {src}")
    dst = packs_root / relpath / gen_id
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    marker = dst / ".publish_in_progress"
    marker.write_text("", encoding="utf-8")

    file_digests = {}
    checksum_lines = []
    for path in sorted(src.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        target = dst / path.name
        shutil.copy2(path, target)
        digest = sha256_file(target)
        file_digests[path.name] = {"sha256": digest, "bytes": target.stat().st_size}
        checksum_lines.append(f"{digest}  {path.name}")

    navi_manifest_name = region.get("manifest")
    client_manifest = {
        "schema": 1,
        "generation": gen_id,
        "region_id": relpath,
        "bake_id": bake_id,
        "stem": region.get("stem"),
        "has_delta_h": bool(region.get("has_delta_h")),
        "navi_manifest": navi_manifest_name,
        "files": file_digests,
    }
    (dst / "manifest.json").write_text(
        json.dumps(client_manifest, indent=2) + "\n", encoding="utf-8"
    )
    (dst / "checksums.sha256").write_text(
        "\n".join(checksum_lines) + ("\n" if checksum_lines else ""),
        encoding="utf-8",
    )
    marker.unlink(missing_ok=True)

    for stale in complete_gens(packs_root / relpath)[1 + keep_prior :]:
        shutil.rmtree(stale, ignore_errors=True)

    print(f"published packs/{relpath}/{gen_id}/ bake_id={bake_id} files={len(file_digests)}")

public_regions = rebuild_current_json(published, generation=gen_id)
print(f"wrote {published / 'current.json'} regions={len(public_regions)}")
PY

log_info "PUBLISHED generation=${GEN_ID} live=${NAVI_LIVE_LINK} -> ${FINAL}"
log_info "HTTP static root=${NAVI_PUBLISHED_DIR} (GET/HEAD only via navi-packs vhost)"
