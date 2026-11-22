#!/usr/bin/env bash
# Promote NR-filled bake-tree sidecars into publish-staging + published
# without flipping the live version or re-baking wetland/VL/elevation.
#
# Typical cause: NR-only rebake updated work/<ver>/sidecars/ but finish/publish
# copied from stale publish-staging (count matched, content did not).
#
# Usage (as root — interactive sudo):
#   sudo bash scripts/mwm/promote-nr-sidecars.sh --dry-run --version 261115
#   sudo bash scripts/mwm/promote-nr-sidecars.sh --version 261115
#
# Default LIVE tree: /media/navi/navi-server
set -euo pipefail

LIVE="${NAVI_SERVER_LIVE:-/media/navi/navi-server}"
SERVICE_USER="${NAVI_SERVICE_USER:-navit-server}"
VER="${NAVI_MWM_PROMOTE_VERSION:-261115}"
DRY_RUN=0
SIDECAR_BIN="${NAVI_MWM_SIDECAR_BIN:-}"
VERIFY_LEAF="${NAVI_MWM_PROMOTE_VERIFY_LEAF:-Norway_Oppland}"
EXPECT_NR="${NAVI_MWM_PROMOTE_EXPECT_NR:-1205}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --version) VER="$2"; shift 2 ;;
    --live) LIVE="$2"; shift 2 ;;
    --verify-leaf) VERIFY_LEAF="$2"; shift 2 ;;
    --expect-nr) EXPECT_NR="$2"; shift 2 ;;
    --sidecar-bin) SIDECAR_BIN="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

WORK="${LIVE}/data/mwm/maps_build/comaps-release/${VER}"
BAKE="${WORK}/sidecars"
STG="${WORK}/publish-staging/${VER}"
PUB="${LIVE}/data/published/mwm/${VER}"
CUR="${LIVE}/data/published/mwm/current.json"
CAT="${PUB}/catalog.json"
SUM="${PUB}/checksums.sha256"

if [[ "$(id -u)" -ne 0 && "$DRY_RUN" -ne 1 ]]; then
  echo "re-run as root: sudo bash $0 --version ${VER}" >&2
  exit 1
fi

[[ -d "$BAKE" ]] || { echo "missing bake sidecars: $BAKE" >&2; exit 1; }
[[ -d "$STG" ]] || { echo "missing publish-staging: $STG" >&2; exit 1; }
[[ -d "$PUB" ]] || { echo "missing published: $PUB" >&2; exit 1; }
[[ -f "$CAT" ]] || { echo "missing catalog: $CAT" >&2; exit 1; }
[[ -f "$CUR" ]] || { echo "missing current.json: $CUR" >&2; exit 1; }

# Refuse to flip/change version away from VER.
CUR_VER="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version") or "")' "$CUR")"
[[ "$CUR_VER" == "$VER" ]] || {
  echo "FATAL: current.json version=${CUR_VER@Q} != ${VER@Q} — refusing (do not change live version)" >&2
  exit 1
}

if [[ -z "$SIDECAR_BIN" ]]; then
  for cand in \
    "${LIVE}/target/release/navi-mwm-sidecar" \
    /media/navi/haaken-scratch/navi-server-mwm/target/release/navi-mwm-sidecar \
    /media/navi/haaken-scratch/navi-server-mwm/target/debug/navi-mwm-sidecar
  do
    if [[ -x "$cand" ]]; then SIDECAR_BIN="$cand"; break; fi
  done
fi

python3 - "$BAKE" "$STG" "$PUB" "$CAT" "$CUR" "$SUM" "$DRY_RUN" "$SERVICE_USER" \
  "$VERIFY_LEAF" "$EXPECT_NR" "${SIDECAR_BIN:-}" <<'PY'
import hashlib, json, os, shutil, subprocess, sys
from pathlib import Path

bake, stg, pub = map(Path, sys.argv[1:4])
cat_path, cur_path, sum_path = map(Path, sys.argv[4:7])
dry = sys.argv[7] == "1"
user = sys.argv[8]
verify_leaf = sys.argv[9]
expect_nr = int(sys.argv[10] or "0")
sidecar_bin = sys.argv[11] or ""

def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

mismatched = []
for bp in sorted(bake.glob("*.navi-sidecar.v1.zst")):
    pp = pub / bp.name
    if not pp.is_file() or bp.stat().st_size != pp.stat().st_size:
        mismatched.append(bp)
    elif bp.stat().st_mtime_ns != pp.stat().st_mtime_ns:
        # same size; only promote if content differs (rare)
        if sha256(bp) != sha256(pp):
            mismatched.append(bp)

print(f"version stays: {json.loads(cat_path.read_text()).get('version')}")
print(f"bake sidecars: {len(list(bake.glob('*.navi-sidecar.v1.zst')))}")
print(f"would promote: {len(mismatched)} sidecars (size/sha != published)")
bytes_delta = 0
for bp in mismatched:
    pp = pub / bp.name
    old = pp.stat().st_size if pp.is_file() else 0
    bytes_delta += bp.stat().st_size - old
print(f"net bytes delta: {bytes_delta:+d}")
print("--- files ---")
for bp in mismatched:
    pp = pub / bp.name
    sp = stg / bp.name
    print(
        f"{bp.name}\tbake={bp.stat().st_size}"
        f"\tpub={pp.stat().st_size if pp.is_file() else 'MISSING'}"
        f"\tstg={sp.stat().st_size if sp.is_file() else 'MISSING'}"
    )

if dry:
    print("DRY-RUN: no writes")
    sys.exit(0)

if not mismatched:
    print("nothing to promote")
    sys.exit(0)

updated = {}
for bp in mismatched:
    for dst_dir in (stg, pub):
        dst = dst_dir / bp.name
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        shutil.copy2(bp, tmp)
        os.replace(tmp, dst)
        try:
            shutil.chown(dst, user=user, group=user)
        except PermissionError:
            pass
    updated[bp.name] = {
        "sha256": sha256(pub / bp.name),
        "bytes": (pub / bp.name).stat().st_size,
        "leaf": bp.name[: -len(".navi-sidecar.v1.zst")],
    }
    print(f"promoted {bp.name} -> staging + published ({updated[bp.name]['bytes']} bytes)")

def patch_catalog(path: Path) -> int:
    body = json.loads(path.read_text())
    n = 0
    by_id = {e["id"]: e for e in body.get("leaves") or [] if isinstance(e, dict) and "id" in e}
    for meta in updated.values():
        e = by_id.get(meta["leaf"])
        if not e:
            print(f"WARN catalog missing leaf {meta['leaf']}", flush=True)
            continue
        e["sidecar_sha256"] = meta["sha256"]
        e["sidecar_bytes"] = meta["bytes"]
        n += 1
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n")
    os.replace(tmp, path)
    try:
        shutil.chown(path, user=user, group=user)
    except PermissionError:
        pass
    return n

n_cat = patch_catalog(cat_path)
n_cur = patch_catalog(cur_path)
print(f"patched catalog leaves={n_cat} current leaves={n_cur}")

if sum_path.is_file():
    lines = []
    wanted = {f"{m['leaf']}.navi-sidecar.v1.zst": m["sha256"] for m in updated.values()}
    for ln in sum_path.read_text().splitlines():
        if not ln.strip():
            continue
        parts = ln.split()
        if len(parts) >= 2 and parts[-1] in wanted:
            lines.append(f"{wanted[parts[-1]]}  {parts[-1]}")
            del wanted[parts[-1]]
        else:
            lines.append(ln)
    for name, h in sorted(wanted.items()):
        lines.append(f"{h}  {name}")
    tmp = sum_path.with_suffix(sum_path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, sum_path)
    try:
        shutil.chown(sum_path, user=user, group=user)
    except PermissionError:
        pass
    print(f"patched {sum_path.name}")

# Verify Oppland (or configured leaf) without touching wetland/VL.
if sidecar_bin and Path(sidecar_bin).is_file():
    sc = pub / f"{verify_leaf}.navi-sidecar.v1.zst"
    proc = subprocess.run(
        [sidecar_bin, "inspect", "--sidecar", str(sc)],
        check=True,
        capture_output=True,
        text=True,
    )
    info = json.loads(proc.stdout)
    nr = int(info.get("named_routes_records") or 0)
    print(
        f"verify {verify_leaf}: named_routes_records={nr} "
        f"wetland={info.get('wetland_records')} vl={info.get('vehicle_limits_records')}"
    )
    if expect_nr and nr != expect_nr:
        raise SystemExit(f"VERIFY FAIL: expected named_routes_records={expect_nr} got {nr}")
    # Catalog sha must match file.
    cat = json.loads(cat_path.read_text())
    entry = next(e for e in cat["leaves"] if e.get("id") == verify_leaf)
    file_sha = sha256(sc)
    if entry.get("sidecar_sha256") != file_sha:
        raise SystemExit("VERIFY FAIL: catalog sidecar_sha256 != published file")
    if entry.get("sidecar_bytes") != sc.stat().st_size:
        raise SystemExit("VERIFY FAIL: catalog sidecar_bytes != published file")
    cov = (entry.get("named_routes_coverage") or {}).get("routes")
    print(f"verify catalog coverage.routes={cov} sidecar_bytes={entry.get('sidecar_bytes')}")
else:
    print("WARN: no navi-mwm-sidecar binary; skipped inspect verify")

print("DONE: NR sidecars promoted; live version unchanged")
PY
