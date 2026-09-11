#!/usr/bin/env bash
# Validation / test checklist for a pack generation tree.
# Confirms checksums (when available), pack presence/non-empty, manifest
# references, and size ballparks vs PBF + previous generation.
#
# Exits non-zero on hard failures. Size outliers print FLAG lines and also
# fail the run (not quiet logs) so a weekly bake cannot silently publish junk.
#
# Size bands default to NAVI_SIZE_* globals. Optional per-region overrides
# are trailing key=value fields on regions.conf lines (e.g.
# wetland_max_ratio=1.0); when applied, OK/FLAG lines note "(region override)".
# terrain_class=polar_sparse relaxes ONLY graph_min to
# NAVI_SIZE_GRAPH_MIN_RATIO_POLAR_SPARSE (default 0.001).
# terrain_class=wetland_heavy relaxes ONLY wetland_max to
# NAVI_SIZE_WETLAND_MAX_RATIO_WETLAND_HEAVY (default 1.0).
# terrain_class=dense_network relaxes ONLY graph_max to
# NAVI_SIZE_GRAPH_MAX_RATIO_DENSE_NETWORK (default 28.0).
# Multiple classes allowed: terrain_class=wetland_heavy,dense_network
# (comma or +). Explicit *_ratio keys still win when both set.
#
# Usage:
#   ./validate-packs.sh /path/to/generation
#   ./validate-packs.sh --region hedmark /path/to/generation
#   ./validate-packs.sh --from-convert              # validate scratch/convert/<region>/
#   ./validate-packs.sh --from-convert --region hedmark

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config
require_cmd python3

FILTER_REGION=""
GEN_DIR=""
FROM_CONVERT=0
TMP_WRAP=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region) FILTER_REGION="$2"; shift 2 ;;
    --from-convert) FROM_CONVERT=1; shift ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    *)
      GEN_DIR="$1"
      shift
      ;;
  esac
done

if [[ "$FROM_CONVERT" -eq 1 ]]; then
  # Wrap scratch/convert/<region_id>/ into a temporary generation-shaped tree.
  TMP_WRAP="$(mktemp -d "${NAVI_SCRATCH_DIR}/validate-wrap.XXXXXX")"
  mkdir -p "${TMP_WRAP}/regions"
  if [[ -n "$FILTER_REGION" ]]; then
    [[ -d "${NAVI_CONVERT_DIR}/${FILTER_REGION}" ]] \
      || die "convert output missing: ${NAVI_CONVERT_DIR}/${FILTER_REGION}"
    ln -s "${NAVI_CONVERT_DIR}/${FILTER_REGION}" "${TMP_WRAP}/regions/${FILTER_REGION}"
  else
    local_any=0
    for d in "${NAVI_CONVERT_DIR}"/*; do
      [[ -d "$d" ]] || continue
      local_any=1
      ln -s "$d" "${TMP_WRAP}/regions/$(basename "$d")"
    done
    [[ "$local_any" -eq 1 ]] || die "no convert output under ${NAVI_CONVERT_DIR}"
  fi
  # Minimal generation manifest so the catalog check is advisory only.
  printf '{"schema":1,"generation":"convert-scratch","regions":[]}\n' \
    >"${TMP_WRAP}/generation-manifest.json"
  GEN_DIR="$TMP_WRAP"
  trap 'rm -rf "$TMP_WRAP"' EXIT
fi

[[ -n "$GEN_DIR" ]] || die "usage: validate-packs.sh [--region ID] GENERATION_DIR | --from-convert"
[[ -d "$GEN_DIR" ]] || die "generation dir missing: $GEN_DIR"

PREV_LIVE=""
if [[ -L "$NAVI_LIVE_LINK" || -d "$NAVI_LIVE_LINK" ]]; then
  PREV_LIVE="$(readlink -f "$NAVI_LIVE_LINK" 2>/dev/null || true)"
fi

export NAVI_SIZE_GRAPH_MIN_RATIO NAVI_SIZE_GRAPH_MAX_RATIO
export NAVI_SIZE_GRAPH_MIN_RATIO_POLAR_SPARSE
export NAVI_SIZE_GRAPH_MAX_RATIO_DENSE_NETWORK
export NAVI_SIZE_POI_MIN_RATIO NAVI_SIZE_POI_MAX_RATIO
export NAVI_SIZE_WETLAND_MIN_RATIO NAVI_SIZE_WETLAND_MAX_RATIO
export NAVI_SIZE_WETLAND_MAX_RATIO_WETLAND_HEAVY
export NAVI_SIZE_TOTAL_MIN_RATIO NAVI_SIZE_TOTAL_MAX_RATIO
export NAVI_SIZE_VS_PREV_MAX_FACTOR
export NAVI_EXTRACTS_DIR FILTER_REGION GEN_DIR PREV_LIVE NAVI_REGIONS_CONF

python3 <<'PY'
import json, os, sys
from pathlib import Path

gen = Path(os.environ["GEN_DIR"])
extracts = Path(os.environ["NAVI_EXTRACTS_DIR"])
prev = os.environ.get("PREV_LIVE") or ""
filter_region = os.environ.get("FILTER_REGION") or ""
regions_conf = Path(os.environ.get("NAVI_REGIONS_CONF") or "")

def ratio_env(name, default):
    return float(os.environ.get(name, default))

# Global defaults (config.env / common.sh). Unlisted regions keep these.
default_bands = {
    "graph": (ratio_env("NAVI_SIZE_GRAPH_MIN_RATIO", "0.15"),
              ratio_env("NAVI_SIZE_GRAPH_MAX_RATIO", "1.20")),
    "poi": (ratio_env("NAVI_SIZE_POI_MIN_RATIO", "0.02"),
            ratio_env("NAVI_SIZE_POI_MAX_RATIO", "0.40")),
    "wetland": (ratio_env("NAVI_SIZE_WETLAND_MIN_RATIO", "0.005"),
                ratio_env("NAVI_SIZE_WETLAND_MAX_RATIO", "0.50")),
    "total": (ratio_env("NAVI_SIZE_TOTAL_MIN_RATIO", "0.10"),
              ratio_env("NAVI_SIZE_TOTAL_MAX_RATIO", "2.00")),
}
vs_prev_max = float(os.environ.get("NAVI_SIZE_VS_PREV_MAX_FACTOR", "3.0"))

# Optional trailing key=value on regions.conf lines (after source).
# Keys: {graph,poi,wetland,total}_{min,max}_ratio
# Plus terrain_class=polar_sparse | wetland_heavy | dense_network (see below).
_OVERRIDE_KEYS = {
    "graph_min_ratio": ("graph", 0),
    "graph_max_ratio": ("graph", 1),
    "poi_min_ratio": ("poi", 0),
    "poi_max_ratio": ("poi", 1),
    "wetland_min_ratio": ("wetland", 0),
    "wetland_max_ratio": ("wetland", 1),
    "total_min_ratio": ("total", 0),
    "total_max_ratio": ("total", 1),
}

# Pre-declared geography classes. Do NOT widen global bands for untagged
# regions. polar_sparse: extreme road sparsity vs PBF (floor still catches
# empty/near-empty graphs). wetland_heavy: mire / mangrove / coastal-marsh /
# delta extracts where wetland pack/PBF is predictably high (ceiling still
# catches runaway duplication, e.g. ratio 3+). dense_network: fine-grained
# residential/service tagging → high graph pack/PBF (ceiling still catches
# runaway duplication well above 28).
_TERRAIN_CLASS_GRAPH_MIN = {
    "polar_sparse": ratio_env("NAVI_SIZE_GRAPH_MIN_RATIO_POLAR_SPARSE", "0.001"),
}
_TERRAIN_CLASS_GRAPH_MAX = {
    "dense_network": ratio_env("NAVI_SIZE_GRAPH_MAX_RATIO_DENSE_NETWORK", "28.0"),
}
_TERRAIN_CLASS_WETLAND_MAX = {
    "wetland_heavy": ratio_env("NAVI_SIZE_WETLAND_MAX_RATIO_WETLAND_HEAVY", "1.0"),
}

def _parse_terrain_classes(raw: str):
    """Split terrain_class=a,b or a+b into unique lowercase tokens."""
    out = []
    for tok in raw.replace("+", ",").split(","):
        c = tok.strip().lower()
        if c and c not in out:
            out.append(c)
    return out

def _merge_terrain(dst: dict, rid: str, classes: list):
    cur = dst.get(rid) or []
    if isinstance(cur, str):
        cur = [cur]
    for c in classes:
        if c not in cur:
            cur.append(c)
    dst[rid] = cur

def load_region_band_meta(conf: Path):
    """Parse per-region size-band overrides + terrain_class. Additive only."""
    overrides_out = {}
    terrain_out = {}
    if not conf.is_file():
        return overrides_out, terrain_out
    for raw in conf.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        rid = parts[0]
        # parts[1] is source; rest are optional key=value
        overrides = {}
        classes = []
        for tok in parts[2:]:
            if "=" not in tok:
                continue
            k, v = tok.split("=", 1)
            k = k.strip().lower()
            v = v.strip()
            if k == "terrain_class":
                classes.extend(_parse_terrain_classes(v))
                continue
            if k not in _OVERRIDE_KEYS:
                continue
            overrides[k] = float(v)
        if overrides:
            overrides_out[rid] = overrides
        if classes:
            _merge_terrain(terrain_out, rid, classes)
    return overrides_out, terrain_out

region_overrides, region_terrain = load_region_band_meta(regions_conf)
# Also merge overrides from the weekly regions.conf when planet conf is active,
# so per-region band overrides (e.g. hedmark wetland_max_ratio) still apply.
_extra_overrides = os.environ.get("NAVI_REGIONS_OVERRIDES_CONF", "").strip()
if not _extra_overrides:
    _pack_root = os.environ.get("NAVI_PACK_ROOT", "")
    if _pack_root:
        _cand = Path(_pack_root) / "regions.conf"
        if _cand.is_file() and _cand.resolve() != regions_conf.resolve():
            _extra_overrides = str(_cand)
if _extra_overrides:
    _ov, _tc = load_region_band_meta(Path(_extra_overrides))
    for _rid, _vals in _ov.items():
        region_overrides.setdefault(_rid, {}).update(_vals)
    for _rid, _classes in _tc.items():
        _merge_terrain(region_terrain, _rid, _classes if isinstance(_classes, list) else [_classes])

def bands_for_region(rid: str):
    """Return (bands_dict, kind->note dict for OK/FLAG suffix)."""
    bands = {k: (lo, hi) for k, (lo, hi) in default_bands.items()}
    notes = {}
    terrains = region_terrain.get(rid) or []
    if isinstance(terrains, str):
        terrains = [terrains]
    for terrain in terrains:
        if terrain in _TERRAIN_CLASS_GRAPH_MIN:
            lo, hi = bands["graph"]
            bands["graph"] = (_TERRAIN_CLASS_GRAPH_MIN[terrain], hi)
            notes["graph"] = f" (terrain_class={terrain})"
        if terrain in _TERRAIN_CLASS_GRAPH_MAX:
            lo, hi = bands["graph"]
            bands["graph"] = (lo, _TERRAIN_CLASS_GRAPH_MAX[terrain])
            notes["graph"] = f" (terrain_class={terrain})"
        if terrain in _TERRAIN_CLASS_WETLAND_MAX:
            lo, hi = bands["wetland"]
            bands["wetland"] = (lo, _TERRAIN_CLASS_WETLAND_MAX[terrain])
            notes["wetland"] = f" (terrain_class={terrain})"
    for key, val in region_overrides.get(rid, {}).items():
        kind, idx = _OVERRIDE_KEYS[key]
        lo, hi = bands[kind]
        if idx == 0:
            bands[kind] = (val, hi)
        else:
            bands[kind] = (lo, val)
        # Explicit numeric override wins over terrain_class for the note too.
        notes[kind] = " (region override)"
    return bands, notes

failures = []
flags = []

def fail(msg):
    failures.append(msg)
    print(f"FAIL: {msg}", flush=True)

def flag(msg):
    flags.append(msg)
    print(f"FLAG: {msg}", flush=True)

def ok(msg):
    print(f"OK: {msg}", flush=True)

def mib(n):
    return n / (1024 * 1024)

def sum_sizes(paths):
    return sum(p.stat().st_size for p in paths if p.is_file())

def load_manifest(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)

def referenced_files(man: dict):
    """Pack files the manifest claims must exist (deduped; prefer tiles)."""
    files = []
    graph_tiles = man.get("graph_tiles") or {}
    if any(graph_tiles.values()):
        for tiles in graph_tiles.values():
            for t in tiles:
                files.append(t["file"])
    else:
        for name in (man.get("graph_files") or {}).values():
            files.append(name)
    pb = man.get("poi_barrier_file")
    if pb:
        files.append(pb)
    wetland_tiles = man.get("wetland_tiles") or []
    if wetland_tiles:
        for t in wetland_tiles:
            files.append(t["file"])
    else:
        wf = man.get("wetland_file")
        if wf:
            files.append(wf)
    # Preserve order but drop duplicates (defensive).
    seen = set()
    out = []
    for f in files:
        if f in seen:
            continue
        seen.add(f)
        out.append(f)
    return out

def rkyv_header_sane(path: Path) -> bool:
    # Fixed 8-byte preamble (magic + version) + rkyv body. Empty wetland packs
    # for arid islands can be only slightly larger than the preamble.
    try:
        sz = path.stat().st_size
        if sz < 8:
            return False
        with path.open("rb") as f:
            head = f.read(8)
        if len(head) < 8:
            return False
        magic = int.from_bytes(head[0:4], "little")
        # NVRK / NVPB / NVWL
        if magic not in (0x4E56524B, 0x4E565042, 0x4E56574C):
            return False
        # Graph packs (NVRK) must be FlatGraphPack format v8.
        if magic == 0x4E56524B:
            ver = int.from_bytes(head[4:8], "little")
            if ver != 8:
                return False
        return True
    except OSError:
        return False

regions_root = gen / "regions"
if not regions_root.is_dir():
    fail(f"missing regions/ under {gen}")
    print(f"SUMMARY failures={len(failures)} flags={len(flags)}")
    sys.exit(1)

region_dirs = sorted(p for p in regions_root.iterdir() if p.is_dir())
if filter_region:
    region_dirs = [p for p in region_dirs if p.name == filter_region]

if not region_dirs:
    fail("no region directories to validate")
    sys.exit(1)

catalog_regions = []
gen_manifest_path = gen / "generation-manifest.json"

for region_dir in region_dirs:
    rid = region_dir.name
    print(f"== region {rid} ==")
    manifests = list(region_dir.glob("*.navi-manifest.json"))
    if not manifests:
        fail(f"{rid}: no *.navi-manifest.json")
        continue
    if len(manifests) > 1:
        flag(f"{rid}: multiple manifests { [m.name for m in manifests] } — validating all")

    pbf = extracts / f"{rid}-latest.osm.pbf"
    pbf_bytes = pbf.stat().st_size if pbf.is_file() else 0
    if pbf_bytes == 0:
        fail(f"{rid}: source PBF missing or empty at {pbf}")
    else:
        ok(f"{rid}: source PBF {mib(pbf_bytes):.1f} MiB")

    md5_sidecar = Path(str(pbf) + ".md5")
    if pbf.is_file() and md5_sidecar.is_file():
        expected = md5_sidecar.read_text(encoding="utf-8").split()[0]
        import hashlib
        h = hashlib.md5()
        with pbf.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        actual = h.hexdigest()
        if actual != expected:
            fail(f"{rid}: PBF md5 mismatch expected={expected} actual={actual}")
        else:
            ok(f"{rid}: PBF md5 OK")

    for man_path in manifests:
        try:
            man = load_manifest(man_path)
        except Exception as e:
            fail(f"{rid}: manifest parse error {man_path.name}: {e}")
            continue
        ok(f"{rid}: manifest {man_path.name} schema={man.get('schema')} stem={man.get('stem')}")

        refs = referenced_files(man)
        if not refs:
            fail(f"{rid}: manifest lists no pack files")
            continue

        missing = []
        empty = []
        corrupt = []
        graph_paths = []
        poi_paths = []
        wet_paths = []

        for rel in refs:
            p = region_dir / rel
            if not p.is_file():
                missing.append(rel)
                continue
            sz = p.stat().st_size
            if sz == 0:
                empty.append(rel)
                continue
            if rel.endswith(".rkyv") and not rkyv_header_sane(p):
                corrupt.append(rel)
            if "navi-graph-" in rel:
                graph_paths.append(p)
            elif "navi-poi-barrier" in rel:
                poi_paths.append(p)
            elif "navi-wetland" in rel:
                wet_paths.append(p)

        if missing:
            fail(f"{rid}: manifest references missing files: {missing}")
        else:
            ok(f"{rid}: all {len(refs)} manifest files exist")
        if empty:
            fail(f"{rid}: empty pack files: {empty}")
        if corrupt:
            fail(f"{rid}: pack files look corrupt/too small: {corrupt}")

        # Required pack types
        if not graph_paths:
            fail(f"{rid}: no graph pack (.navi-graph-*.rkyv)")
        else:
            ok(f"{rid}: graph packs n={len(graph_paths)} size={mib(sum_sizes(graph_paths)):.1f} MiB")
        if not poi_paths:
            fail(f"{rid}: no poi-barrier pack")
        else:
            ok(f"{rid}: poi-barrier size={mib(sum_sizes(poi_paths)):.1f} MiB")
        if not wet_paths:
            fail(f"{rid}: no wetland pack")
        else:
            ok(f"{rid}: wetland size={mib(sum_sizes(wet_paths)):.1f} MiB")

        if pbf_bytes > 0:
            g = sum_sizes(graph_paths)
            p = sum_sizes(poi_paths)
            w = sum_sizes(wet_paths)
            total = g + p + w
            checks = [
                ("graph", g),
                ("poi", p),
                ("wetland", w),
                ("total", total),
            ]
            region_bands, band_notes = bands_for_region(rid)
            for kind, sz in checks:
                if sz <= 0:
                    continue
                r = sz / pbf_bytes
                lo, hi = region_bands[kind]
                band_note = band_notes.get(kind, "")
                msg = (
                    f"{rid}: {kind} ratio={r:.3f} "
                    f"(pack={mib(sz):.1f} MiB / pbf={mib(pbf_bytes):.1f} MiB) "
                    f"band=[{lo},{hi}]{band_note}"
                )
                if r < lo or r > hi:
                    flag(msg + " OUT OF BAND")
                else:
                    ok(msg)

        # Compare vs previous live generation when present.
        if prev:
            prev_region = Path(prev) / "regions" / rid
            if prev_region.is_dir():
                def prev_sum(pattern):
                    return sum_sizes(list(prev_region.glob(pattern)))
                for label, cur, pattern in [
                    ("graph", sum_sizes(graph_paths), "*.navi-graph-*.rkyv"),
                    ("poi", sum_sizes(poi_paths), "*.navi-poi-barrier.rkyv"),
                    ("wetland", sum_sizes(wet_paths), "*.navi-wetland*.rkyv"),
                ]:
                    old = prev_sum(pattern)
                    if old <= 0 or cur <= 0:
                        continue
                    factor = max(cur / old, old / cur)
                    if factor > vs_prev_max:
                        flag(
                            f"{rid}: {label} size vs previous live changed ×{factor:.2f} "
                            f"(old={mib(old):.1f} MiB new={mib(cur):.1f} MiB; max={vs_prev_max})"
                        )
                    else:
                        ok(f"{rid}: {label} vs previous ×{factor:.2f}")

        catalog_regions.append({
            "region_id": rid,
            "stem": man.get("stem"),
            "manifest": man_path.name,
            "has_delta_h": bool(man.get("has_delta_h")),
            "profiles": sorted(man.get("graph_files", {}).keys())
                or sorted(man.get("graph_tiles", {}).keys()),
        })

# Generation-level catalog
gm_path = gen / "generation-manifest.json"
if not gm_path.is_file():
    flag(f"generation-manifest.json missing under {gen} (publish step should write it)")
else:
    ok("generation-manifest.json present")
    try:
        gm = json.loads(gm_path.read_text(encoding="utf-8"))
        # convert-scratch wraps intentionally omit region entries.
        if gm.get("generation") != "convert-scratch":
            for entry in gm.get("regions", []):
                rid = entry.get("region_id")
                man_rel = entry.get("manifest")
                if not rid or not man_rel:
                    fail(f"generation-manifest entry incomplete: {entry}")
                    continue
                p = gen / "regions" / rid / man_rel
                if not p.is_file():
                    fail(f"generation-manifest references missing {p}")
    except Exception as e:
        fail(f"generation-manifest.json parse error: {e}")

print("---")
print(f"SUMMARY failures={len(failures)} flags={len(flags)}")
# Size FLAG lines are treated as hard failures for publish gating.
if failures or flags:
    if flags and not failures:
        print("FAILED: size sanity flags raised (not quiet) — fix or widen bands consciously", flush=True)
    sys.exit(1)
print("PASS: validation checklist OK", flush=True)
PY
