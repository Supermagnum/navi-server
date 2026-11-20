#!/usr/bin/env bash
# Atomically publish one MWM release under published/mwm/{version}/.
# current.json is written last.
# Modes:
#   default: copy MWMs + sidecars (generate mode)
#   --sidecars-only: publish Navi sidecars + catalog v2 (comaps-release)
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

VERSION_DIR=""
PUBLISH_ROOT="${NAVI_MWM_PUBLISHED_ROOT}"
SIDECARS_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version-dir) VERSION_DIR="$2"; shift 2 ;;
    --publish-root) PUBLISH_ROOT="$2"; shift 2 ;;
    --sidecars-only) SIDECARS_ONLY=1; shift ;;
    *) mwm_die "unknown arg: $1" ;;
  esac
done
[[ -n "$VERSION_DIR" && -d "$VERSION_DIR" ]] || mwm_die "need --version-dir"
mkdir -p "$PUBLISH_ROOT"
# Ownership marker so .rkyv scrub/publish refuse to touch this tree.
python3 - "$PUBLISH_ROOT" "${SCRIPT_DIR}/../lib" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from rkyv_published_guard import ensure_mwm_owned_marker
print("mwm marker", ensure_mwm_owned_marker(Path(sys.argv[1])))
PY

TAG="$(mwm_pin_field tag)"
SHA="$(mwm_pin_field sha)"
VER_NAME="$(basename "$VERSION_DIR")"
STAGING="${PUBLISH_ROOT}/.staging/${VER_NAME}"
FINAL="${PUBLISH_ROOT}/${VER_NAME}"

rm -rf "$STAGING"
mkdir -p "$STAGING"

python3 - "$VERSION_DIR" "$STAGING" "$NAVI_MWM_LEAFSET" "$TAG" "$SHA" \
  "$NAVI_MWM_REGION_MAP" "$SIDECARS_ONLY" "$SCRIPT_DIR" <<'PY'
import hashlib, json, os, shutil, sys
from pathlib import Path

version = Path(sys.argv[1])
staging = Path(sys.argv[2])
leafset = Path(sys.argv[3])
tag, sha = sys.argv[4], sys.argv[5]
region_map = Path(sys.argv[6])
sidecars_only = sys.argv[7] == "1"
script_dir = Path(sys.argv[8])
sys.path.insert(0, str(script_dir / "lib"))
from non_routable import NON_ROUTABLE_LEAVES, is_non_routable_leaf, non_routable_catalog_entry  # noqa: E402

leaves = [ln.strip() for ln in leafset.read_text().splitlines() if ln.strip() and not ln.strip().startswith("#")]

def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

discovery = {}
processed = {}
disc_path = version / "discovery.json"
proc_path = version / "processed.json"
if disc_path.is_file():
    discovery = json.loads(disc_path.read_text())
if proc_path.is_file():
    processed = json.loads(proc_path.read_text()).get("leaves") or {}

policy_version = int(os.environ.get("NAVI_MWM_ELEV_POLICY_VERSION", "1") or "1")

leaf_entries = []
checksums = {}
seen = set()
for leaf in leaves:
    seen.add(leaf)
    if is_non_routable_leaf(leaf):
        disc_leaf = (discovery.get("leaves") or {}).get(leaf) or {}
        entry = non_routable_catalog_entry(leaf, disc_leaf)
        entry["elevation_policy_version"] = None
        leaf_entries.append(entry)
        continue
    sc = version / f"{leaf}.navi-sidecar.v1.zst"
    mwm = version / f"{leaf}.mwm"
    if not sc.is_file():
        # Allow partial Nordic runs: skip missing sidecars with warning to stdout
        print(f"WARN missing sidecar {leaf}", flush=True)
        continue
    dst_s = staging / f"{leaf}.navi-sidecar.v1.zst"
    shutil.copy2(sc, dst_s)
    ss = sha256(dst_s)
    checksums[f"{leaf}.navi-sidecar.v1.zst"] = ss
    entry = {
        "id": leaf,
        "sidecar_sha256": ss,
        "sidecar_bytes": dst_s.stat().st_size,
        "sidecar_url": f"{staging.name}/{leaf}.navi-sidecar.v1.zst",
        "shared": False,
        "no_sidecar": False,
        "elevation_policy_version": policy_version,
    }
    vl_report = version / "meta" / "vehicle_limits" / f"{leaf}.report.json"
    if not vl_report.is_file():
        # Bake stores reports under work/meta; version_dir is often publish-staging/<ver>.
        alt = version.parent.parent / "meta" / "vehicle_limits" / f"{leaf}.report.json"
        if alt.is_file():
            vl_report = alt
    if vl_report.is_file():
        try:
            rep = json.loads(vl_report.read_text())
            tagged = int(rep.get("tagged_ways") or 0)
            matched = int(rep.get("matched_ways") or 0)
            nn = int(rep.get("non_numeric_no_limit") or 0)
            unk = int(rep.get("unknown_value") or 0)
            rate = rep.get("match_rate")
            if rate is None:
                skipped = int(rep.get("skipped_unparseable") or 0)
                numeric = max(0, tagged - skipped)
                rate = (matched / numeric) if numeric else 0.0
            map_rel = str(rep.get("map_release") or "").strip()
            if not map_rel:
                map_rel = str(discovery.get("version") or version.name)
            entry["vehicle_limits_coverage"] = {
                "tagged_ways": tagged,
                "matched_ways": matched,
                "match_rate": float(rate),
                "non_numeric_no_limit": nn,
                "unknown_value": unk,
                "osm_data_date": str(rep.get("osm_data_date") or ""),
                "map_release": map_rel,
            }
        except Exception as exc:
            print(f"WARN vehicle_limits coverage {leaf}: {exc}", flush=True)
    if sidecars_only:
        meta = processed.get(leaf) or {}
        disc_leaf = (discovery.get("leaves") or {}).get(leaf) or {}
        entry.update({
            "comaps_file": f"{leaf}.mwm",
            "comaps_bytes": meta.get("comaps_bytes") or disc_leaf.get("size"),
            "comaps_sha1_base64": meta.get("comaps_sha1_base64") or disc_leaf.get("sha1_base64"),
            "mwm_bytes": meta.get("mwm_bytes"),
            "mwm_sha256": meta.get("mwm_sha256"),
            "mwm_fingerprint": {
                "size": meta.get("mwm_bytes"),
                "sha256": meta.get("mwm_sha256"),
            },
        })
    else:
        if not mwm.is_file():
            raise SystemExit(f"missing mwm for {leaf}")
        dst_m = staging / f"{leaf}.mwm"
        shutil.copy2(mwm, dst_m)
        ms = sha256(dst_m)
        checksums[f"{leaf}.mwm"] = ms
        entry.update({
            "mwm_bytes": dst_m.stat().st_size,
            "mwm_sha256": ms,
        })
    leaf_entries.append(entry)

# Always list non-routable overlays from discovery even if absent from leafset.
for leaf in sorted(NON_ROUTABLE_LEAVES):
    if leaf in seen:
        continue
    if leaf not in (discovery.get("leaves") or {}):
        continue
    entry = non_routable_catalog_entry(leaf, discovery["leaves"][leaf])
    entry["elevation_policy_version"] = None
    leaf_entries.append(entry)

attr_src = version / "attribution.json"
if attr_src.is_file():
    shutil.copy2(attr_src, staging / "attribution.json")
else:
    raise SystemExit("attribution.json required")
for name in ("ATTRIBUTION.md", "discovery.json", "processed.json"):
    src = version / name
    if src.is_file():
        shutil.copy2(src, staging / name)

mapping = json.loads(region_map.read_text())
shared = mapping.get("shared_leaves") or {}
for e in leaf_entries:
    if e["id"] in shared:
        e["shared"] = True
        e["shared_with_regions"] = shared[e["id"]]

# Optional region-migration + country_groups produced by build-region-mapping.py
# (copied into the version dir before publish, or next to region_map).
migration_src = version / "region-migration.json"
if not migration_src.is_file():
    migration_src = region_map.parent / "region-migration.json"
country_src = version / "country_groups.json"
if not country_src.is_file():
    country_src = region_map.parent / "country_groups.json"
overrides_src = Path(os.environ.get(
    "NAVI_MWM_DISPLAY_OVERRIDES",
    str(script_dir.parent.parent / "config" / "mwm" / "catalog" / "display_name_overrides.json"),
))
country_groups = None
display_overrides = None
if country_src.is_file():
    cg = json.loads(country_src.read_text())
    country_groups = cg.get("countries") if isinstance(cg, dict) and "countries" in cg else cg
    display_overrides = cg.get("display_name_overrides") if isinstance(cg, dict) else None
if display_overrides is None and overrides_src.is_file():
    ov = json.loads(overrides_src.read_text())
    display_overrides = {
        "leaves": ov.get("leaves") or {},
        "likely_outdated_elsewhere": ov.get("likely_outdated_elsewhere") or [],
    }
if migration_src.is_file():
    shutil.copy2(migration_src, staging / "region-migration.json")
if country_src.is_file():
    shutil.copy2(country_src, staging / "country_groups.json")

(staging / "checksums.sha256").write_text(
    "".join(f"{h}  {name}\n" for name, h in sorted(checksums.items()))
)

region_to_leaves = {
    rid: [c["leaf"] for c in (meta.get("comaps_leaves") or [])]
    for rid, meta in (mapping.get("navi_region_to_comaps_leaves") or {}).items()
}

if sidecars_only:
    catalog = {
        "schema": "navi-mwm-catalog/v2",
        "mode": "comaps-release",
        "version": None,
        "comaps": {
            "map_series": discovery.get("map_series"),
            "release_version": discovery.get("version"),
            "countries_txt_sha256": discovery.get("countries_txt_sha256"),
            "countries_txt_bytes": discovery.get("countries_txt_bytes"),
            "countries_txt_sig_verified": discovery.get("countries_txt_sig_verified"),
            "countries_txt_sig_sha256": discovery.get("countries_txt_sig_sha256"),
            "countries_txt_pubkey_hex": discovery.get("countries_txt_pubkey_hex"),
            "metaserver_url": discovery.get("metaserver_url"),
            "maps_json_url": f"{(discovery.get('metaserver_url') or '').rstrip('/')}/meta/maps.json",
            "note": "Clients discover mirrors via metaserver /servers; no hard-coded mirror list",
        },
        "generator": {"tag": tag, "sha": sha, "role": "navi-sidecar-tools-pin"},
        "limitation": mapping.get("limitation")
            or "Base MWMs come from a single CoMaps planet release; Navi publishes sidecars only.",
        "leaves": leaf_entries,
        "region_to_leaves": region_to_leaves,
        "shared_leaves": shared,
        "world_leaf_count": len((discovery.get("leaves") or {})),
        "elevation_policy_version": policy_version,
        "non_routable_leaves": sorted(NON_ROUTABLE_LEAVES),
    }
    if country_groups is not None:
        catalog["country_groups"] = country_groups
        catalog["country_groups_url"] = None  # filled after version is known
    if display_overrides is not None:
        catalog["display_name_overrides"] = display_overrides
    if migration_src.is_file():
        catalog["region_migration_url"] = None  # filled after version is known
else:
    catalog = {
        "schema": "navi-mwm-catalog/v1",
        "mode": "generate",
        "version": None,
        "generator": {"tag": tag, "sha": sha},
        "limitation": mapping.get("limitation")
            or "Cross-border routing out of the Nordic leaf set is not supported in the pilot.",
        "leaves": leaf_entries,
        "region_to_leaves": region_to_leaves,
        "shared_leaves": shared,
    }
(staging / "catalog.body.json").write_text(json.dumps(catalog, indent=2) + "\n")
print("staged", len(leaf_entries), "leaves", "sidecars_only" if sidecars_only else "full")
PY

python3 - "$STAGING" "$VER_NAME" <<'PY'
import json, sys
from pathlib import Path
staging = Path(sys.argv[1])
ver = sys.argv[2]
body = json.loads((staging / "catalog.body.json").read_text())
body["version"] = ver
if body.get("schema") == "navi-mwm-catalog/v2":
    if (staging / "region-migration.json").is_file():
        body["region_migration_url"] = f"{ver}/region-migration.json"
    if (staging / "country_groups.json").is_file():
        body["country_groups_url"] = f"{ver}/country_groups.json"
(staging / "catalog.json").write_text(json.dumps(body, indent=2) + "\n")
(staging / "catalog.body.json").unlink()
PY

rm -rf "$FINAL"
mv "$STAGING" "$FINAL"

"${SCRIPT_DIR}/retain-mwm.sh" --publish-root "$PUBLISH_ROOT" --protect "$VER_NAME"

TMP_CURRENT="${PUBLISH_ROOT}/current.json.tmp.$$"
python3 - "$FINAL/catalog.json" "$TMP_CURRENT" <<'PY'
import json, sys
from pathlib import Path
cat = json.loads(Path(sys.argv[1]).read_text())
Path(sys.argv[2]).write_text(json.dumps(cat, indent=2) + "\n")
PY
mv -f "$TMP_CURRENT" "${PUBLISH_ROOT}/current.json"
mwm_log "published ${FINAL}; current.json -> ${VER_NAME}"
