#!/usr/bin/env bash
# Safe single-region publish for smoke runs (sequential or concurrent).
#
# - flock serialize finalize so current.json / live link stay consistent
# - never wipe packs/<path>/ before the new generation is confirmed
# - write new gen beside any existing last-known-good; prune old gens only
#   for that region after success (NAVI_PUBLISHED_KEEP_PER_REGION, default 1
#   previous = keep latest + 1 prior)
# - mark generations with .smoke_in_progress while writing; remove when done
# - prune only finished gens that are not live and not in-progress
# - rebuild current.json from the published packs tree (untouched regions stay)
# - HTTP layout uses Geofabrik paths (asia/china/anhui), matching the Navi app
#   download-scope picker — not flat bake ids (asia_china_anhui)

publish_single_region_safe() {
  local rid="$1"
  local gen_id="$2"
  local src="${NAVI_CONVERT_DIR}/${rid}"
  local final="${NAVI_GENERATIONS_DIR}/${gen_id}"
  local relpath pub
  relpath="$(region_publish_relpath "$rid")"
  pub="${NAVI_PUBLISHED_DIR}/packs/${relpath}/${gen_id}"
  local lock="${NAVI_PACK_ROOT}/.publish.lock"
  local marker="${final}/.smoke_in_progress"
  local pub_marker="${pub}/.publish_in_progress"
  local keep_n="${NAVI_PUBLISHED_KEEP_PER_REGION:-1}"

  [[ -d "$src" ]] || return 1
  "${SCRIPT_DIR}/validate-packs.sh" --from-convert --region "$rid" || return 1

  mkdir -p "$final/regions"
  : >"$marker"
  rm -rf "${final}/regions/${rid}"
  mkdir -p "${final}/regions/${rid}"
  find "$src" -maxdepth 1 -type f \( -name '*.rkyv' -o -name '*.navi-manifest.json' \) \
    -exec cp -a {} "${final}/regions/${rid}/" \;

  mkdir -p "$(dirname "$lock")"
  # Serialize publish finalize across concurrent workers.
  (
    flock 9

    # Write into a fresh gen dir beside any existing last-known-good for this
    # region. Never rm -rf packs/<path>/ — a mid-publish failure must leave the
    # previous generation intact.
    if [[ -e "$pub" ]]; then
      # Same gen_id collision (should be rare after collision-proof IDs).
      rm -rf "$pub"
    fi
    mkdir -p "$pub"
    : >"$pub_marker"

    python3 - "$final/regions/$rid" "$rid" "$relpath" "$gen_id" "$pub" \
        "${NAVI_PUBLISHED_DIR}" "$keep_n" "${SCRIPT_DIR}/lib" <<'PY'
import hashlib, json, os, shutil, sys
from pathlib import Path

sys.path.insert(0, sys.argv[8])
from published_tree import complete_gens, rebuild_current_json

src = Path(sys.argv[1])
bake_id = sys.argv[2]
relpath = sys.argv[3]
gen_id = sys.argv[4]
pub = Path(sys.argv[5])
published = Path(sys.argv[6])
keep_prior = max(0, int(sys.argv[7]))

pub.mkdir(parents=True, exist_ok=True)
files = {}
lines = []
for path in sorted(src.iterdir()):
    if not path.is_file() or path.name.startswith("."):
        continue
    dest = pub / path.name
    shutil.copy2(path, dest)
    h = hashlib.sha256()
    with dest.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    digest = h.hexdigest()
    files[path.name] = {"sha256": digest, "bytes": dest.stat().st_size}
    lines.append(f"{digest}  {path.name}")

mans = sorted(pub.glob("*.navi-manifest.json"))
navi_man = mans[0].name if mans else None
has_dh = False
if navi_man:
    try:
        has_dh = bool(
            json.loads((pub / navi_man).read_text(encoding="utf-8")).get("has_delta_h")
        )
    except Exception:
        has_dh = False

man = {
    "schema": 1,
    "generation": gen_id,
    "region_id": relpath,
    "bake_id": bake_id,
    "stem": navi_man.replace(".navi-manifest.json", "") if navi_man else bake_id,
    "has_delta_h": has_dh,
    "navi_manifest": navi_man,
    "files": files,
}
def _atomic_write_text(path, text):
    partial = path.with_name(path.name + ".partial")
    partial.write_text(text, encoding="utf-8")
    partial.replace(path)

_atomic_write_text(pub / "manifest.json", json.dumps(man, indent=2) + "\n")
_atomic_write_text(pub / "checksums.sha256", "\n".join(lines) + "\n")

# Drop in-progress marker only after manifests are durable.
marker = pub / ".publish_in_progress"
if marker.exists():
    marker.unlink()

# Prune older complete gens for THIS region only (keep latest + keep_prior).
region_root = pub.parent
for stale in complete_gens(region_root)[1 + keep_prior :]:
    shutil.rmtree(stale, ignore_errors=True)

regions = rebuild_current_json(
    published,
    generation=gen_id,
    extra={"smoke": "planet-geofabrik-leaves"},
)
print(
    f"published {relpath} bake_id={bake_id} gen={gen_id} files={len(files)} "
    f"regions_total={len(regions)} has_delta_h={has_dh} "
    f"kept_prior={keep_prior}"
)
PY

    ln -sfn "$final" "${NAVI_LIVE_LINK}.new"
    mv -Tf "${NAVI_LIVE_LINK}.new" "$NAVI_LIVE_LINK"
    printf '%s\n' "$gen_id" >"${NAVI_PACK_ROOT}/CURRENT_GENERATION"
    rm -f "$marker"

    # Prune finished gens that are not live and not still marked in-progress.
    # These are internal staging trees; HTTP clients use published/packs/.
    local live_target keep
    live_target="$(readlink -f "$NAVI_LIVE_LINK" 2>/dev/null || true)"
    for keep in "${NAVI_GENERATIONS_DIR}"/*; do
      [[ -d "$keep" ]] || continue
      [[ -e "${keep}/.smoke_in_progress" ]] && continue
      if [[ -n "$live_target" && "$(readlink -f "$keep" 2>/dev/null || true)" == "$live_target" ]]; then
        continue
      fi
      [[ "$(basename "$keep")" == "$gen_id" ]] && continue
      rm -rf "$keep"
    done
  ) 9>"$lock"
}
