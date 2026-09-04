#!/usr/bin/env bash
# Validation / test checklist for a pack generation tree.
# Confirms checksums (when available), pack presence/non-empty, manifest
# references, and size ballparks vs PBF + previous generation.
#
# Exits non-zero on hard failures. Size outliers print FLAG lines and also
# fail the run (not quiet logs) so a weekly bake cannot silently publish junk.
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
export NAVI_SIZE_POI_MIN_RATIO NAVI_SIZE_POI_MAX_RATIO
export NAVI_SIZE_WETLAND_MIN_RATIO NAVI_SIZE_WETLAND_MAX_RATIO
export NAVI_SIZE_TOTAL_MIN_RATIO NAVI_SIZE_TOTAL_MAX_RATIO
export NAVI_SIZE_VS_PREV_MAX_FACTOR
export NAVI_EXTRACTS_DIR FILTER_REGION GEN_DIR PREV_LIVE

python3 <<'PY'
import json, os, sys
from pathlib import Path

gen = Path(os.environ["GEN_DIR"])
extracts = Path(os.environ["NAVI_EXTRACTS_DIR"])
prev = os.environ.get("PREV_LIVE") or ""
filter_region = os.environ.get("FILTER_REGION") or ""

def ratio_env(name, default):
    return float(os.environ.get(name, default))

bands = {
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
        return magic in (0x4E56524B, 0x4E565042, 0x4E56574C)
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
            for kind, sz in checks:
                if sz <= 0:
                    continue
                r = sz / pbf_bytes
                lo, hi = bands[kind]
                msg = f"{rid}: {kind} ratio={r:.3f} (pack={mib(sz):.1f} MiB / pbf={mib(pbf_bytes):.1f} MiB) band=[{lo},{hi}]"
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
