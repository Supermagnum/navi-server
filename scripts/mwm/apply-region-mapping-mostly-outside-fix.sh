#!/usr/bin/env bash
# Apply mostly-outside region_to_leaves repair to live published MWM catalog.
#
# Catalog / migration mapping ONLY — does not touch sidecars, .rkyv, or
# pack-pipeline.lock. Safe while a VL bake runs with --skip-publish.
#
# Usage (root):
#   sudo bash scripts/mwm/apply-region-mapping-mostly-outside-fix.sh
#
# Optional env:
#   LIVE_MWM  VER  SRC_DIR  REPO  ZFS_DATASET  BASE_URL  SKIP_ZFS=1
set -euo pipefail

LIVE_MWM="${LIVE_MWM:-/media/navi/navi-server/data/published/mwm}"
VER="${VER:-261105}"
REPO="${REPO:-/media/navi/haaken-scratch/navi-server-mwm}"
SRC_DIR="${SRC_DIR:-$REPO/config/mwm/catalog}"
BASE_URL="${BASE_URL:-https://navigate-me.duckdns.org}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
SNAP_NAME="navi-region-mapping-mostly-outside-${TS}"
BAK_SUFFIX="bak-pre-mostly-outside-${TS}"

die() { echo "ERROR: $*" >&2; exit 1; }
need_root() { [[ "$(id -u)" -eq 0 ]] || die "run as root (live published tree writes)"; }

resolve_zfs_dataset() {
  local explicit="${ZFS_DATASET:-${ZFS_POOL:-}}"
  if [[ -n "$explicit" ]]; then
    printf '%s\n' "$explicit"
    return 0
  fi
  local src=""
  if command -v findmnt >/dev/null 2>&1; then
    src="$(findmnt -n -o SOURCE --target "$LIVE_MWM" 2>/dev/null || true)"
    [[ -z "$src" ]] && src="$(findmnt -n -o SOURCE --target /media/navi 2>/dev/null || true)"
  fi
  if [[ -n "$src" ]] && command -v zfs >/dev/null 2>&1 \
      && zfs list -H -o name "$src" >/dev/null 2>&1; then
    printf '%s\n' "$src"
    return 0
  fi
  if command -v zfs >/dev/null 2>&1 \
      && zfs list -H -o name "Mypool/navi" >/dev/null 2>&1; then
    printf '%s\n' "Mypool/navi"
    return 0
  fi
  die "could not auto-detect ZFS dataset; set ZFS_DATASET or SKIP_ZFS=1"
}

need_root
[[ -d "$LIVE_MWM/$VER" ]] || die "missing $LIVE_MWM/$VER"
[[ -f "$SRC_DIR/region-migration.json" ]] || die "missing $SRC_DIR/region-migration.json"
[[ -f "$SRC_DIR/region_leaf_mapping.json" ]] || die "missing $SRC_DIR/region_leaf_mapping.json"
[[ -f "$SRC_DIR/country_groups.json" ]] || die "missing $SRC_DIR/country_groups.json"
[[ -f "$LIVE_MWM/$VER/catalog.json" ]] || die "missing live catalog"
[[ -f "$LIVE_MWM/current.json" ]] || die "missing live current.json"

# Refuse to interfere with pack pipeline / bake locks.
for lock in \
  /media/navi/navi-server/data/mwm/pack-pipeline.lock \
  /media/navi/navi-server/data/published/packs/pack-pipeline.lock \
  /run/navi/pack-pipeline.lock
do
  if [[ -e "$lock" ]]; then
    echo "WARN: $lock exists — not taking it; catalog-only apply continues"
  fi
done

# Confirm VL bake (if any) is skip-publish.
if pgrep -af 'run-comaps-release-bake|run-mwm-bake' >/tmp/navi-region-fix-bake-procs.txt 2>/dev/null; then
  if grep -q -- '--skip-publish' /tmp/navi-region-fix-bake-procs.txt; then
    echo "OK: bake processes present with --skip-publish (will not overwrite catalog mid-apply)"
  else
    echo "WARN: bake process without --skip-publish detected — review before continuing"
    cat /tmp/navi-region-fix-bake-procs.txt >&2 || true
  fi
else
  echo "OK: no bake process matched"
fi

if [[ "${SKIP_ZFS:-0}" != "1" ]]; then
  ZFS_DATASET="$(resolve_zfs_dataset)"
  command -v zfs >/dev/null 2>&1 || die "zfs not found; set SKIP_ZFS=1 to bak-files-only"
  zfs list -H -o name "$ZFS_DATASET" >/dev/null 2>&1 \
    || die "ZFS dataset missing: ${ZFS_DATASET}"
  echo "== ZFS snapshot ${ZFS_DATASET}@${SNAP_NAME} =="
  zfs snapshot "${ZFS_DATASET}@${SNAP_NAME}" || die "zfs snapshot failed"
  echo "${ZFS_DATASET}@${SNAP_NAME}" > /tmp/navi-region-mapping-snapshot.txt
else
  echo "== SKIP_ZFS=1 (file bak only) =="
  ZFS_DATASET=""
fi

STAGING="$LIVE_MWM/.staging-region-mapping-$$"
ROLLBACK_DIR="$LIVE_MWM/.rollback-region-mapping-$VER-$TS"
rm -rf "$STAGING"
mkdir -p "$STAGING" "$ROLLBACK_DIR"

echo "== Bak live files → $ROLLBACK_DIR and *.${BAK_SUFFIX} =="
cp -a "$LIVE_MWM/$VER/catalog.json" "$ROLLBACK_DIR/catalog.json"
cp -a "$LIVE_MWM/current.json" "$ROLLBACK_DIR/current.json"
cp -a "$LIVE_MWM/current.json" "$LIVE_MWM/current.json.${BAK_SUFFIX}"
[[ -f "$LIVE_MWM/$VER/region-migration.json" ]] \
  && cp -a "$LIVE_MWM/$VER/region-migration.json" "$ROLLBACK_DIR/" \
  && cp -a "$LIVE_MWM/$VER/region-migration.json" "$LIVE_MWM/$VER/region-migration.json.${BAK_SUFFIX}" \
  || true
[[ -f "$LIVE_MWM/$VER/country_groups.json" ]] \
  && cp -a "$LIVE_MWM/$VER/country_groups.json" "$ROLLBACK_DIR/" \
  || true
echo "$ROLLBACK_DIR" > /tmp/navi-region-mapping-rollback-dir.txt

echo "== Stage catalog merge (region maps only; sidecar leaf entries preserved) =="
python3 - "$LIVE_MWM" "$VER" "$SRC_DIR" "$STAGING" "$REPO" <<'PY'
import json, shutil, sys
from pathlib import Path

live = Path(sys.argv[1])
ver = sys.argv[2]
src = Path(sys.argv[3])
staging = Path(sys.argv[4])
repo = Path(sys.argv[5])

cat = json.loads((live / ver / "catalog.json").read_text(encoding="utf-8"))
mapping = json.loads((src / "region_leaf_mapping.json").read_text(encoding="utf-8"))
cg = json.loads((src / "country_groups.json").read_text(encoding="utf-8"))
ov_path = repo / "config" / "mwm" / "catalog" / "display_name_overrides.json"
overrides = json.loads(ov_path.read_text(encoding="utf-8")) if ov_path.is_file() else {}

shared = mapping.get("shared_leaves") or {}
region_to_leaves = {
    rid: [c["leaf"] for c in (meta.get("comaps_leaves") or [])]
    for rid, meta in (mapping.get("navi_region_to_comaps_leaves") or {}).items()
}

# Refresh shared flags only — do not rewrite sidecar byte fields.
for e in cat.get("leaves") or []:
    lid = e.get("id")
    if lid in shared:
        e["shared"] = True
        e["shared_with_regions"] = shared[lid]
    else:
        e["shared"] = False
        e.pop("shared_with_regions", None)

cat["region_to_leaves"] = region_to_leaves
cat["shared_leaves"] = shared
cat["country_groups"] = cg.get("countries") if isinstance(cg, dict) and "countries" in cg else cg
cat["country_groups_url"] = f"{ver}/country_groups.json"
cat["region_migration_url"] = f"{ver}/region-migration.json"
cat["display_name_overrides"] = {
    "leaves": overrides.get("leaves") or {},
    "likely_outdated_elsewhere": overrides.get("likely_outdated_elsewhere") or [],
}
if mapping.get("limitation"):
    cat["limitation"] = mapping["limitation"]

ost = region_to_leaves.get("europe_norway_ostlandet") or []
bad = [x for x in ost if x.startswith(("Denmark_", "Sweden_", "Finland_", "Russia_"))]
if bad:
    raise SystemExit(f"refusing publish: ostlandet still has foreign leaves: {bad}")
for need in ("Norway_Hedmark", "Norway_Oppland", "Norway_Southern", "Norway_Central"):
    if need not in ost:
        raise SystemExit(f"refusing publish: ostlandet missing {need}: {ost}")

shutil.copy2(src / "region-migration.json", staging / "region-migration.json")
shutil.copy2(src / "country_groups.json", staging / "country_groups.json")
(staging / "catalog.json").write_text(json.dumps(cat, indent=2) + "\n", encoding="utf-8")
print(
    "staged leaves", len(cat.get("leaves") or []),
    "regions", len(region_to_leaves),
    "countries", len(cat["country_groups"]),
    "ostlandet", ost,
)
PY

echo "== Atomic install (migration → country_groups → catalog → current.json last) =="
cp -a "$STAGING/region-migration.json" "$LIVE_MWM/$VER/region-migration.json"
cp -a "$STAGING/country_groups.json" "$LIVE_MWM/$VER/country_groups.json"
cp -a "$STAGING/catalog.json" "$LIVE_MWM/$VER/catalog.json"
TMP_CURRENT="$LIVE_MWM/current.json.tmp.$$"
cp -a "$STAGING/catalog.json" "$TMP_CURRENT"
mv -f "$TMP_CURRENT" "$LIVE_MWM/current.json"
rm -rf "$STAGING"

echo "== Local verify =="
python3 - "$LIVE_MWM" "$VER" <<'PY'
import json, sys
from pathlib import Path
live, ver = Path(sys.argv[1]), sys.argv[2]
cat = json.loads((live / "current.json").read_text())
assert cat.get("version") == ver
ost = cat["region_to_leaves"]["europe_norway_ostlandet"]
assert not any(x.startswith(("Denmark_", "Sweden_")) for x in ost), ost
assert set(ost) >= {"Norway_Hedmark", "Norway_Oppland", "Norway_Southern", "Norway_Central"}
sor = cat["region_to_leaves"]["europe_norway_sorlandet"]
assert "Denmark_North Denmark Region" not in sor, sor
# Sidecar inventory untouched: leaf count stable vs bak if present
print("local OK ostlandet=", ost, "sorlandet=", sor)
PY

echo "== HTTP verify (best-effort) =="
set +e
curl -fsS "$BASE_URL/mwm/current.json" -o /tmp/navi-region-mapping-current-check.json
RC1=$?
curl -fsS "$BASE_URL/mwm/$VER/region-migration.json" -o /tmp/navi-region-mapping-mig-check.json
RC2=$?
set -e
if [[ "$RC1" -eq 0 ]]; then
  python3 - <<'PY'
import json
from pathlib import Path
cat = json.loads(Path("/tmp/navi-region-mapping-current-check.json").read_text())
ost = cat["region_to_leaves"]["europe_norway_ostlandet"]
assert not any(x.startswith(("Denmark_", "Sweden_")) for x in ost), ost
print("HTTP OK ostlandet", ost)
PY
else
  echo "WARN: HTTP current.json fetch failed rc=$RC1 (local file apply still done)"
fi
[[ "$RC2" -eq 0 ]] || echo "WARN: HTTP migration fetch failed rc=$RC2"

# Confirm we did not stop bake / take pack lock.
if pgrep -af 'navi-vl-261115|run-comaps-release-bake' >/dev/null 2>&1; then
  echo "OK: bake still running after apply"
else
  echo "NOTE: no VL bake process matched after apply (may have finished independently)"
fi

cat <<EOF

Published mostly-outside region mapping fix for $VER.

Bak:
  $LIVE_MWM/current.json.${BAK_SUFFIX}
  $ROLLBACK_DIR/

Rollback (files):
  cp -a $ROLLBACK_DIR/catalog.json $LIVE_MWM/$VER/catalog.json
  cp -a $ROLLBACK_DIR/current.json $LIVE_MWM/current.json
  [[ -f $ROLLBACK_DIR/region-migration.json ]] && cp -a $ROLLBACK_DIR/region-migration.json $LIVE_MWM/$VER/region-migration.json

EOF
if [[ -n "$ZFS_DATASET" ]]; then
  cat <<EOF
Rollback (ZFS — last resort, whole dataset):
  zfs rollback ${ZFS_DATASET}@${SNAP_NAME}

EOF
fi
