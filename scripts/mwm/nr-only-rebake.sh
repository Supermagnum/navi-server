#!/usr/bin/env bash
# FAST path: refill empty NR01 without re-baking wetland / VL / elevation.
#
# Why this works
# --------------
# process_comaps_leaf.py deletes tmp/<leaf>/*.jsonl + MWM after each leaf, so
# there is nothing to "reuse" as JSONL. But the finished sidecars already hold
# wetland / boardwalk / elevation / vehicle_limits + the MWM fingerprint.
# annotate-named-routes only needs the NR PBF + leaf .poly (no roads dump).
# navi-mwm-sidecar repack-named-routes keeps other sections and rewrites NR01.
#
# Does NOT publish. Does NOT download MWMs. Does NOT clear processed.json leaves
# (only updates sidecar_sha256 after each repack).
#
# Suggested sequence (one sudo prompt each, or combine):
#   sudo bash /tmp/navi-nr-261115-nr-only-rebake.sh --smoke-one-leaf
#   # if smoke shows named_routes_records ~>1200 on Oppland (ski included):
#   sudo nohup bash /tmp/navi-nr-261115-nr-only-rebake.sh --rebake-all \
#     >/tmp/navi-nr-261115-nr-only.log 2>&1 &
#
set -euo pipefail

LIVE="${NAVI_SERVER_LIVE:-/media/navi/navi-server}"
SCRATCH="${NAVI_SCRATCH_REPO:-/media/navi/haaken-scratch/navi-server-mwm}"
ENV_FILE="${NAVI_MWM_ENV_FILE:-/etc/navi/mwm.env}"
SERVICE_USER="${NAVI_SERVICE_USER:-navit-server}"
VER=261115
WORK="${LIVE}/data/mwm/maps_build/comaps-release/${VER}"
LEAF_SMOKE="${NAVI_NR_SMOKE_LEAF:-Norway_Oppland}"
NR_PBF_DEFAULT=/media/navi/haaken-scratch/osm-named-routes/planet-named-routes.osm.pbf
NR_PBF="${NAVI_MWM_NAMED_ROUTES_PBF:-$NR_PBF_DEFAULT}"
CONC="${NAVI_NR_ONLY_CONCURRENCY:-4}"
PARALLEL_PY="${NAVI_NR_ONLY_PARALLEL_PY:-/tmp/navi-nr-261115-nr-only-parallel.py}"
# Prefer durable scratch copy if /tmp helper was wiped.
if [[ ! -f "$PARALLEL_PY" && -f "${SCRATCH}/scripts/mwm/nr-only-rebake-parallel.py" ]]; then
  PARALLEL_PY="${SCRATCH}/scripts/mwm/nr-only-rebake-parallel.py"
fi
LOG_DEFAULT="/tmp/navi-nr-261115-nr-only.log"
MIN_PBF_BYTES=1000000

MODE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h) MODE=help; shift ;;
    --dry-run) MODE=dry-run; shift ;;
    --sync-all) MODE=sync-all; shift ;;
    --smoke-one-leaf) MODE=smoke; shift ;;
    --rebake-all) MODE=all; shift ;;
    --concurrency) CONC="$2"; shift 2 ;;
    *) echo "unknown: $1 (try --help)" >&2; exit 2 ;;
  esac
done

help() {
  cat <<'EOF'
NR-only rebake for 261115 (keep wetland / VL / elevation)
=========================================================

Per-leaf path: annotate-named-routes → navi-mwm-sidecar repack-named-routes
into the existing sidecar (preserves wetland/elev/VL/mwm fingerprint).

ETA (Europe/Oslo, concurrency 4, ~14–20s/leaf measured): ~1–1.5 h for 1159 leaves.
Full leaf rebake is ~16 h; this path skips download+dump+wetland+VL+elev.

Commands (sudo — interactive sudo once; no password inside script)
-----------------------------------------------------------------
  # optional inspect (no root):
  bash /tmp/navi-nr-261115-nr-only-rebake.sh --dry-run

  # smoke one leaf (~15–40s after cargo sync/build):
  sudo bash /tmp/navi-nr-261115-nr-only-rebake.sh --smoke-one-leaf

  # full world NR-only (nohup-friendly):
  sudo nohup bash /tmp/navi-nr-261115-nr-only-rebake.sh --rebake-all \
    >/tmp/navi-nr-261115-nr-only.log 2>&1 &
  tail -f /tmp/navi-nr-261115-nr-only.log

Does NOT publish. Does NOT clear processed for wetland redo.

Env
---
  NAVI_MWM_NAMED_ROUTES_PBF   restored ~121MB PBF (required, >=1MB)
  NAVI_NR_ONLY_CONCURRENCY    default 4
  NAVI_NR_SMOKE_LEAF          default Norway_Oppland
  NAVI_NR_ONLY_PARALLEL_PY    default /tmp/…-parallel.py
EOF
}

[[ "${MODE:-}" == "help" || -z "${MODE:-}" ]] && { help; exit 0; }

# dry-run is read-only and does not need root
if [[ "$MODE" != "dry-run" && "$(id -u)" -ne 0 ]]; then
  echo "re-run as root: sudo bash $0 --${MODE}" >&2
  exit 1
fi

bin_has_repack() {
  local bin="$1"
  [[ -x "$bin" ]] || return 1
  "$bin" repack-named-routes --help 2>&1 | grep -q -- '--named-routes-jsonl'
}

require_nr_pbf() {
  [[ -f "$NR_PBF" ]] || { echo "FATAL: named-routes PBF missing: $NR_PBF" >&2; exit 1; }
  local nr_bytes
  nr_bytes=$(stat -c%s "$NR_PBF" 2>/dev/null || echo 0)
  if [[ "$nr_bytes" -lt "$MIN_PBF_BYTES" ]]; then
    echo "FATAL: named-routes PBF too small (${nr_bytes} bytes < ${MIN_PBF_BYTES}): $NR_PBF" >&2
    echo "FATAL: refusing empty/tiny PBF (empty-dedupe incident left a 73-byte file)." >&2
    echo "FATAL: restore planet-named-routes.osm.pbf (~121MB) before NR-only rebake." >&2
    exit 1
  fi
  local sha
  sha=$(sha256sum "$NR_PBF" | awk '{print $1}')
  echo "[pbf] NR $NR_PBF size=$nr_bytes sha=${sha:0:16}…"
  if ! runuser -u "$SERVICE_USER" -- test -r "$NR_PBF"; then
    echo "[pbf] granting ACL read to $SERVICE_USER"
    setfacl -m "u:${SERVICE_USER}:r" "$NR_PBF" || \
      setfacl -R -m "u:${SERVICE_USER}:rx" "$(dirname "$NR_PBF")" || true
    setfacl -m "u:${SERVICE_USER}:r" "$NR_PBF"
  fi
  runuser -u "$SERVICE_USER" -- test -r "$NR_PBF" \
    || { echo "FATAL: $SERVICE_USER cannot read $NR_PBF" >&2; exit 1; }
}

load_comaps_root() {
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
  echo "${NAVI_MWM_COMAPS_ROOT}"
}

ensure_parallel_py() {
  # Always stage the helper at a fixed /tmp path for runuser. Default PARALLEL_PY
  # is already that path — install refuses same-file copy, so skip when src==dst.
  local dest=/tmp/navi-nr-261115-nr-only-parallel.py
  local src=""
  if [[ -f "$PARALLEL_PY" ]]; then
    src="$PARALLEL_PY"
  elif [[ -f "${SCRATCH}/scripts/mwm/nr-only-rebake-parallel.py" ]]; then
    src="${SCRATCH}/scripts/mwm/nr-only-rebake-parallel.py"
  else
    echo "FATAL: missing NR-only parallel helper ($PARALLEL_PY)" >&2
    exit 1
  fi
  if [[ "$src" -ef "$dest" ]]; then
    chmod 0755 "$dest"
    if [[ "$(id -u)" -eq 0 ]]; then
      chown root:root "$dest" || true
    fi
  else
    install -o root -g root -m 0755 "$src" "$dest"
  fi
  PARALLEL_PY="$dest"
  chmod 0755 "$PARALLEL_PY"
}

sync_all() {
  local dst="${LIVE}/scripts/mwm"
  local src="${SCRATCH}/scripts/mwm"
  mkdir -p "${dst}/lib" \
    "${LIVE}/crates/navi-mwm-sidecar/src" \
    "${LIVE}/place-source-points/src"

  install -o root -g root -m 0644 "${src}/annotate-named-routes.py" "$dst/"
  install -o root -g root -m 0644 "${src}/process_comaps_leaf.py" "$dst/"
  # Durable copies of NR-only helpers into live scripts (optional reference)
  if [[ -f "${src}/nr-only-rebake-parallel.py" ]]; then
    install -o root -g root -m 0755 "${src}/nr-only-rebake-parallel.py" "$dst/"
  fi
  if [[ -f "${src}/nr-only-rebake.sh" ]]; then
    install -o root -g root -m 0755 "${src}/nr-only-rebake.sh" "$dst/"
  fi
  ensure_parallel_py

  local SC_SRC="${SCRATCH}/crates/navi-mwm-sidecar"
  local SC_DST="${LIVE}/crates/navi-mwm-sidecar"
  local bak="/tmp/navi-mwm-sidecar-nr-only.bak-$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$bak"
  cp -a "${SC_DST}/src" "$bak/" 2>/dev/null || true
  echo "[rust] backup -> $bak"
  for f in lib.rs main.rs vehicle_limits.rs named_routes.rs; do
    [[ -f "${SC_SRC}/src/$f" ]] || { echo "FATAL: missing scratch $f" >&2; exit 1; }
    install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0644 \
      "${SC_SRC}/src/$f" "${SC_DST}/src/$f"
  done
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0644 \
    "${SC_SRC}/Cargo.toml" "${SC_DST}/Cargo.toml"

  # Ensure live workspace knows place-source-points (needed by annotate)
  if ! grep -q 'place-source-points' "${LIVE}/Cargo.toml" 2>/dev/null; then
    echo "[rust] WARN: live root Cargo.toml may lack place-source-points" >&2
    if grep -q 'place-source-points' "${SCRATCH}/Cargo.toml"; then
      install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0644 \
        "${SCRATCH}/Cargo.toml" "${LIVE}/Cargo.toml"
      [[ -f "${SCRATCH}/Cargo.lock" ]] && install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0644 \
        "${SCRATCH}/Cargo.lock" "${LIVE}/Cargo.lock"
      echo "[rust] synced root Cargo.toml/lock from scratch"
    fi
  fi

  local PS_SRC="${SCRATCH}/place-source-points"
  local PS_DST="${LIVE}/place-source-points"
  mkdir -p "${PS_DST}/src"
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0644 \
    "${PS_SRC}/Cargo.toml" "${PS_DST}/Cargo.toml"
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0644 \
    "${PS_SRC}/src/lib.rs" "${PS_DST}/src/lib.rs"
  install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0644 \
    "${PS_SRC}/src/main.rs" "${PS_DST}/src/main.rs"

  echo "[rust] cargo build navi-mwm-sidecar + place-source-points as $SERVICE_USER"
  runuser -u "$SERVICE_USER" -- bash -lc "
    set -euo pipefail
    cd '$LIVE'
    export CARGO_TARGET_DIR='$LIVE/target'
    cargo build -p navi-mwm-sidecar -p place-source-points --release --manifest-path Cargo.toml
  "
  bin_has_repack "${LIVE}/target/release/navi-mwm-sidecar" \
    || { echo "FATAL: live navi-mwm-sidecar lacks repack-named-routes (scratch main.rs sync failed?)" >&2; exit 1; }
  [[ -x "${LIVE}/target/release/place-source-points" ]] \
    || { echo "FATAL: place-source-points missing" >&2; exit 1; }
  echo "[rust] binaries OK (repack-named-routes present)"
}

run_nr_only() {
  local extra=("$@")
  require_nr_pbf
  if systemctl is-active --quiet navi-mwm-bake.service 2>/dev/null || \
     systemctl is-active --quiet navi-mwm-check.service 2>/dev/null; then
    echo "FATAL: bake/check active — stop first" >&2
    exit 1
  fi
  local comaps_root
  comaps_root="$(load_comaps_root)"
  local borders="${comaps_root}/data/borders"
  [[ -d "$borders" ]] || { echo "FATAL: borders missing: $borders" >&2; exit 1; }
  bin_has_repack "${LIVE}/target/release/navi-mwm-sidecar" \
    || { echo "FATAL: run --sync-all first (no repack-named-routes)" >&2; exit 1; }
  ensure_parallel_py

  echo "[nr-only] work=$WORK conc=$CONC NR_PBF=$NR_PBF log hint=${LOG_DEFAULT} extra=${extra[*]:-}"
  # shellcheck disable=SC2086
  runuser -u "$SERVICE_USER" -- bash -c '
    set -euo pipefail
    ulimit -n 65536 || ulimit -n 16384 || true
    set -a
    # shellcheck disable=SC1090
    source "'"$ENV_FILE"'"
    set +a
    export NAVI_MWM_NAMED_ROUTES_PBF="'"$NR_PBF"'"
    export NAVI_PLACE_SOURCE_BIN="'"$LIVE"'/target/release/place-source-points"
    exec python3 /tmp/navi-nr-261115-nr-only-parallel.py \
      --work "'"$WORK"'" \
      --pbf "'"$NR_PBF"'" \
      --borders "'"$borders"'" \
      --annotate-py "'"$LIVE"'/scripts/mwm/annotate-named-routes.py" \
      --sidecar-bin "'"$LIVE"'/target/release/navi-mwm-sidecar" \
      --place-source-bin "'"$LIVE"'/target/release/place-source-points" \
      --concurrency "'"$CONC"'" \
      '"${extra[*]}"'
  '
}

smoke_check() {
  local leaf="$LEAF_SMOKE"
  local sc="${WORK}/sidecars/${leaf}.navi-sidecar.v1.zst"
  echo "=== smoke check $leaf ==="
  [[ -f "$sc" ]] || { echo "SMOKE FAIL: missing $sc"; return 1; }
  local bin="${LIVE}/target/release/navi-mwm-sidecar"
  local info
  info=$("$bin" inspect --sidecar "$sc")
  echo "$info" | python3 -c '
import json,sys
d=json.load(sys.stdin)
nr=int(d.get("named_routes_records") or 0)
print("named_routes_records=", nr)
print("wetland_records=", d.get("wetland_records"))
print("elevation_features=", d.get("elevation_features"))
print("vehicle_limits_records=", d.get("vehicle_limits_records"))
print("sections=", d.get("sections"))
# Local copy smoke with ski-inclusive PBF yielded ~1205 on Oppland; pre-ski was ~931.
# Accept any non-zero; warn if Oppland is oddly low.
sys.exit(0 if nr>0 else 1)
' || { echo "SMOKE FAIL: named_routes_records==0"; return 1; }
  if zstd -dc "$sc" 2>/dev/null | head -c 4000000 | grep -a -E 'named_routes|NR01' | head -3; then
    echo "SMOKE OK: NR01 present in $leaf"
  else
    echo "SMOKE FAIL: no NR01 strings"
    return 1
  fi
  ls -la "${WORK}/meta/named_routes/${leaf}.report.json" 2>/dev/null || true
  python3 - <<PY || true
import json, pathlib
rep = pathlib.Path("${WORK}/meta/named_routes/${leaf}.report.json")
if rep.is_file():
    print("report:", rep.read_text()[:800])
PY
}

case "$MODE" in
  dry-run)
    echo "WORK=$WORK LEAF_SMOKE=$LEAF_SMOKE CONC=$CONC"
    echo "NR_PBF=$NR_PBF PARALLEL_PY=$PARALLEL_PY"
    echo "LOG_DEFAULT=$LOG_DEFAULT"
    echo "path: annotate-named-routes + repack-named-routes (no MWM download, no wetland/elev/VL)"
    echo "does NOT publish; does NOT clear processed leaves"
    if [[ -f "$NR_PBF" ]]; then
      echo "[dry] NR PBF $(stat -c%s "$NR_PBF") bytes"
      if [[ "$(stat -c%s "$NR_PBF")" -lt "$MIN_PBF_BYTES" ]]; then
        echo "[dry] FATAL would refuse: PBF < ${MIN_PBF_BYTES}"
      fi
    else
      echo "[dry] NR PBF MISSING"
    fi
    if bin_has_repack "${SCRATCH}/target/release/navi-mwm-sidecar" 2>/dev/null; then
      echo "[dry] scratch sidecar has repack-named-routes"
    else
      echo "[dry] scratch sidecar: build/sync needed"
    fi
    if bin_has_repack "${LIVE}/target/release/navi-mwm-sidecar" 2>/dev/null; then
      echo "[dry] live sidecar already has repack-named-routes"
    else
      echo "[dry] live sidecar: needs --sync-all (cargo build as $SERVICE_USER)"
    fi
    ls "${WORK}/sidecars" 2>/dev/null | wc -l | awk '{print "[dry] sidecars:", $1}'
    find "${WORK}/tmp" -type f 2>/dev/null | wc -l | awk '{print "[dry] tmp files (expect 0):", $1}'
    if [[ -f "${WORK}/sidecars/${LEAF_SMOKE}.navi-sidecar.v1.zst" ]]; then
      if [[ -x "${SCRATCH}/target/release/navi-mwm-sidecar" ]]; then
        "${SCRATCH}/target/release/navi-mwm-sidecar" inspect \
          --sidecar "${WORK}/sidecars/${LEAF_SMOKE}.navi-sidecar.v1.zst" 2>/dev/null \
          | python3 -c 'import json,sys;d=json.load(sys.stdin);print("[dry]", "'"$LEAF_SMOKE"'", "nr=",d.get("named_routes_records"),"wetland=",d.get("wetland_records"),"vl=",d.get("vehicle_limits_records"))' \
          || true
      fi
    fi
    ;;
  sync-all)
    sync_all
    ;;
  smoke)
    sync_all
    mkdir -p /tmp/nr-only-smoke-bak
    cp -a "${WORK}/sidecars/${LEAF_SMOKE}.navi-sidecar.v1.zst" \
      "/tmp/nr-only-smoke-bak/${LEAF_SMOKE}.navi-sidecar.v1.zst.$(date -u +%Y%m%dT%H%M%SZ)" || true
    run_nr_only --leaf "$LEAF_SMOKE"
    smoke_check
    echo "If OK: sudo nohup bash $0 --rebake-all >${LOG_DEFAULT} 2>&1 &"
    echo "tail -f ${LOG_DEFAULT}"
    ;;
  all)
    sync_all
    echo "[all] NR-only world pass (no processed wipe, no wetland/elev/VL redo)"
    echo "[all] progress JSON lines on stdout — redirect to ${LOG_DEFAULT} under nohup"
    run_nr_only
    echo "DONE NR-only. Spot-check Oppland then finish/publish when ready. Do NOT publish blindly."
    ;;
  *)
    help
    exit 2
    ;;
esac
