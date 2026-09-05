#!/usr/bin/env bash
# Batched planet-leaf bake: sun-order, ~80 GiB scratch budget, publish+clean
# per successful batch. PAUSES on validate failure or crash (does not continue).
#
# Designed to run inside screen/tmux so a disconnected client does not stop it.
#
# Usage:
#   ./run-planet-leaves-batched.sh              # fresh (rebuilds batch plan)
#   ./run-planet-leaves-batched.sh --resume     # skip already-published + done batches
#   ./run-planet-leaves-batched.sh --from-batch N
#
# Stop file: touch ${NAVI_PACK_ROOT}/STOP_PLANET_LEAVES
# Pause marker on failure: ${NAVI_LOG_DIR}/planet-leaves/PAUSED
# pause_run writes PAUSED then holds the process (screen stays up) until killed.
# After a Geofabrik fetch pause exhausted its transient budget: probe / fix, then
# kill the held session and ./run-planet-leaves-batched.sh --resume (or use
# scripts/start-planet-leaves-screen.sh --resume).

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/publish-safe.sh
source "${SCRIPT_DIR}/lib/publish-safe.sh"
load_config

RESUME=0
FROM_BATCH=1
BUDGET_GIB="${NAVI_PLANET_BATCH_SCRATCH_GIB:-80}"
ABORT_PCT="${NAVI_SMOKE_QUOTA_ABORT_PCT:-90}"
REGIONS_FILE="${NAVI_PACK_ROOT}/regions.planet.conf"
BBOX_FILE="${REGIONS_FILE}.bboxes.json"
REPORT_DIR="${NAVI_LOG_DIR}/planet-leaves"
STATE_FILE="${REPORT_DIR}/state.jsonl"
PLAN_FILE="${REPORT_DIR}/batch-plan.json"
PAUSE_FILE="${REPORT_DIR}/PAUSED"
STOP_FILE="${NAVI_PACK_ROOT}/STOP_PLANET_LEAVES"
PROGRESS_FILE="${REPORT_DIR}/progress.txt"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --resume) RESUME=1; shift ;;
    --from-batch) FROM_BATCH="$2"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) die "unknown arg: $1" ;;
  esac
done

export NAVI_BAKE_DELTA_H=1
export NAVI_BAKE_TOWN_ROUTES=0
export NAVI_REGIONS_CONF="$REGIONS_FILE"
: "${NAVI_ELEV_DIR:=${NAVI_PACK_ROOT}/elevation}"
export NAVI_ELEV_DIR
load_config
export NAVI_REGIONS_CONF="$REGIONS_FILE"

mkdir -p "$REPORT_DIR" "${NAVI_PUBLISHED_DIR}/packs"

quota_pct() {
  local used quota
  used="$(zfs get -Hp -o value used "$NAVI_ZFS_DATASET")"
  quota="$(zfs get -Hp -o value quota "$NAVI_ZFS_DATASET")"
  if [[ "$quota" == "0" || "$quota" == "none" ]]; then
    echo 0
    return
  fi
  awk -v u="$used" -v q="$quota" 'BEGIN{printf "%.1f", (u*100)/q}'
}

disk_report() {
  local pct
  pct="$(quota_pct)"
  log_info "disk used_pct=${pct}% avail=$(zfs get -H -o value available "$NAVI_ZFS_DATASET")"
  printf 'disk_used_pct=%s avail=%s\n' "$pct" "$(zfs get -H -o value available "$NAVI_ZFS_DATASET")" >>"$PROGRESS_FILE"
}

pause_run() {
  local reason="$1"
  printf '%s\n' "$reason" >"$PAUSE_FILE"
  echo "{\"event\":\"paused\",\"reason\":$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$reason")}" >>"$STATE_FILE"
  log_fail "PAUSED: ${reason} — see ${PAUSE_FILE}; fix then kill this session and re-run with --resume"
  # Hold instead of exiting: historically `exit 2` plus a wrapper `set -e` made
  # the screen session disappear, so operators only noticed hours later. The
  # session staying up is intentional; do not auto-resume from this hold.
  log_info "PAUSED hold active (orchestrator process stays alive); Ctrl-C / kill session when ready to --resume"
  while true; do
    sleep 300
    log_info "still PAUSED: ${reason}"
  done
}

already_published() {
  region_is_published "$1"
}

cleanup_region_scratch() {
  local rid="$1"
  local pbf
  pbf="$(region_pbf_path "$rid")"
  rm -f "$pbf" "${pbf}.md5" "${pbf}.partial"
  rm -rf "${NAVI_CONVERT_DIR}/${rid}"
  log_info "cleaned scratch for ${rid}"
}

build_batch_plan() {
  python3 - "$REGIONS_FILE" "$PLAN_FILE" "$BUDGET_GIB" <<'PY'
import json, time
from pathlib import Path
import urllib.request
import concurrent.futures as cf

conf = Path(__import__("sys").argv[1])
plan_path = Path(__import__("sys").argv[2])
budget_gib = float(__import__("sys").argv[3])
bbox = json.loads(Path(str(conf) + ".bboxes.json").read_text())

rows = []
for line in conf.read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    parts = line.split()
    rid, src = parts[0], parts[1]
    if not src.startswith("geofabrik:"):
        continue
    path = src.split(":", 1)[1].strip("/")
    rows.append((rid, path))

# Skip path-parents (e.g. north-america/us when states exist under it).
paths = {rid: path for rid, path in rows}
parents = {
    rid
    for rid, path in rows
    if any(p != path and p.startswith(path + "/") for p in paths.values())
}

ids = [rid for rid, _ in rows if rid not in parents]
print(f"skip_parents={sorted(parents)} bake_leaves={len(ids)}", flush=True)

# HEAD sizes
def head_one(rid_path):
    rid, path = rid_path
    url = f"https://download.geofabrik.de/{path}-latest.osm.pbf"
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            cl = r.headers.get("Content-Length")
            return rid, int(cl) if cl else 50 * 1024 * 1024
    except Exception:
        return rid, 50 * 1024 * 1024

sizes = {}
with cf.ThreadPoolExecutor(max_workers=32) as ex:
    for rid, sz in ex.map(head_one, [(r, paths[r]) for r in ids]):
        sizes[rid] = sz

# Sun-order
def norm_lon(lon):
    while lon <= -180:
        lon += 360
    while lon > 180:
        lon -= 360
    return lon

start = time.time()
lon0 = norm_lon(-15.0 * ((start % 86400) / 3600.0))
keyed = []
for i, rid in enumerate(ids):
    b = bbox.get(rid)
    if not b:
        keyed.append((360.0, i, rid))
        continue
    lon = norm_lon((b[1] + b[3]) / 2.0)
    keyed.append(((lon0 - lon) % 360.0, i, rid))
keyed.sort()
ordered = [r for _, _, r in keyed]

RATIO = 7.57
BUDGET = budget_gib * (1024**3)
batches = []
cur = []
cur_bytes = 0
for rid in ordered:
    pbf = sizes[rid]
    scratch = int(pbf * (1 + RATIO))
    if cur and cur_bytes + scratch > BUDGET:
        batches.append({"region_ids": cur, "est_scratch_bytes": cur_bytes})
        cur = []
        cur_bytes = 0
    cur.append(rid)
    cur_bytes += scratch
if cur:
    batches.append({"region_ids": cur, "est_scratch_bytes": cur_bytes})

plan = {
    "skip_parents": sorted(parents),
    "bake_leaves": len(ids),
    "budget_scratch_gib": budget_gib,
    "total_pbf_bytes": sum(sizes.values()),
    "batches": [
        {
            "index": i + 1,
            "n": len(b["region_ids"]),
            "est_scratch_gib": round(b["est_scratch_bytes"] / (1024**3), 2),
            "region_ids": b["region_ids"],
        }
        for i, b in enumerate(batches)
    ],
}
plan_path.write_text(json.dumps(plan) + "\n")
print(f"batches={len(batches)} total_pbf_GiB={plan['total_pbf_bytes']/(1024**3):.1f}", flush=True)
for b in plan["batches"]:
    print(
        f"  batch {b['index']}: n={b['n']} scratch≈{b['est_scratch_gib']}GiB "
        f"{b['region_ids'][0]} .. {b['region_ids'][-1]}",
        flush=True,
    )
PY
}

process_region() {
  local rid="$1"
  local phase rc

  if already_published "$rid"; then
    log_info "skip already published ${rid}"
    echo "{\"event\":\"region_skip\",\"region_id\":\"${rid}\",\"reason\":\"already_published\"}" >>"$STATE_FILE"
    return 0
  fi

  # Resume-friendly: if convert output already validates, do not re-fetch/convert.
  if [[ -d "${NAVI_CONVERT_DIR}/${rid}" ]]; then
    set +e
    "${SCRIPT_DIR}/validate-packs.sh" --from-convert --region "$rid"
    rc=$?
    set -e
    if [[ $rc -eq 0 ]]; then
      log_info "reuse validated convert scratch region=${rid}"
      echo "{\"event\":\"region_validated\",\"region_id\":\"${rid}\",\"reused\":true}" >>"$STATE_FILE"
      return 0
    fi
    log_info "existing convert scratch failed validate — will re-convert region=${rid}"
  fi

  phase="before_fetch:${rid}"
  "${SCRIPT_DIR}/check-disk-quota.sh" || pause_run "disk quota gate failed at ${phase}"
  local pct
  pct="$(quota_pct)"
  if awk -v u="$pct" -v a="$ABORT_PCT" 'BEGIN{exit !(u+0 >= a+0)}'; then
    pause_run "quota ${pct}% >= abort ${ABORT_PCT}% at ${phase}"
  fi

  log_info "==== region ${rid} fetch ===="
  set +e
  # Conditional fetch (ETag) unless force needed; use non-force so resume is cheap.
  "${SCRIPT_DIR}/fetch-extracts.sh" "$rid"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    pause_run "fetch failed region=${rid} rc=${rc}"
  fi

  if [[ -f "$BBOX_FILE" ]]; then
    local bbox
    bbox="$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); b=d.get(sys.argv[2]); print(','.join(map(str,b)) if b else '')" "$BBOX_FILE" "$rid")"
    if [[ -n "$bbox" ]]; then
      set +e
      # Optional --poly: skip DEM cells outside extract boundary (fail-open).
      poly_path="${NAVI_EXTRACTS_DIR}/${rid}.poly"
      dem_args=(--elev-dir "$NAVI_ELEV_DIR" --bbox="$bbox")
      [[ -f "$poly_path" ]] && dem_args+=(--poly "$poly_path")
      python3 "${SCRIPT_DIR}/prefetch-dem-bbox.py" "${dem_args[@]}"
      set -e
    fi
  fi

  phase="pre_convert:${rid}"
  "${SCRIPT_DIR}/check-disk-quota.sh" || pause_run "disk quota gate failed at ${phase}"

  log_info "==== region ${rid} convert ===="
  set +e
  "${SCRIPT_DIR}/convert-region.sh" "$rid"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    pause_run "convert failed/crashed region=${rid} rc=${rc}"
  fi

  log_info "==== region ${rid} validate ===="
  set +e
  "${SCRIPT_DIR}/validate-packs.sh" --from-convert --region "$rid"
  rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    pause_run "validate failed region=${rid} rc=${rc} (convert left at ${NAVI_CONVERT_DIR}/${rid})"
  fi

  echo "{\"event\":\"region_validated\",\"region_id\":\"${rid}\"}" >>"$STATE_FILE"
  return 0
}

publish_and_clean_region() {
  local rid="$1"
  local gen_id
  if already_published "$rid"; then
    cleanup_region_scratch "$rid" || true
    return 0
  fi
  gen_id="$(generation_id "$rid")"
  set +e
  publish_single_region_safe "$rid" "$gen_id"
  local rc=$?
  set -e
  if [[ $rc -ne 0 ]]; then
    pause_run "publish failed region=${rid} rc=${rc}"
  fi
  cleanup_region_scratch "$rid"
  if [[ -f "$BBOX_FILE" && "${NAVI_SMOKE_EVICT_DEM:-1}" == "1" ]]; then
    local bbox
    bbox="$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); b=d.get(sys.argv[2]); print(','.join(map(str,b)) if b else '')" "$BBOX_FILE" "$rid")"
    if [[ -n "$bbox" ]]; then
      python3 "${SCRIPT_DIR}/prefetch-dem-bbox.py" --elev-dir "$NAVI_ELEV_DIR" --bbox="$bbox" --evict || true
    fi
  fi
  echo "{\"event\":\"region_ok\",\"region_id\":\"${rid}\",\"generation\":\"${gen_id}\"}" >>"$STATE_FILE"
}

# --- main ---
[[ -f "$REGIONS_FILE" ]] || die "missing ${REGIONS_FILE}"
[[ -f "$BBOX_FILE" ]] || die "missing ${BBOX_FILE}"

if [[ "$RESUME" -eq 0 || ! -f "$PLAN_FILE" ]]; then
  log_info "building batch plan (skip path-parents, budget=${BUDGET_GIB}GiB)"
  build_batch_plan
fi
[[ -f "$PLAN_FILE" ]] || die "batch plan missing: ${PLAN_FILE}"

rm -f "$PAUSE_FILE"
[[ "$RESUME" -eq 1 ]] || : >"$STATE_FILE"
: >"$PROGRESS_FILE"

log_info "==== planet-leaves batched bake start ===="
log_info "plan=${PLAN_FILE} resume=${RESUME} from_batch=${FROM_BATCH}"
disk_report

mapfile -t BATCH_INDEXES < <(python3 -c "import json; print('\\n'.join(str(b['index']) for b in json.load(open('${PLAN_FILE}'))['batches']))")

START_EPOCH="$(date -u +%s)"
for bi in "${BATCH_INDEXES[@]}"; do
  [[ "$bi" -ge "$FROM_BATCH" ]] || continue
  if [[ -f "$STOP_FILE" ]]; then
    log_info "stop file present ${STOP_FILE}; exiting cleanly"
    echo "{\"event\":\"stopped\",\"at_batch\":${bi}}" >>"$STATE_FILE"
    exit 0
  fi

  mapfile -t REGIONS < <(python3 -c "import json,sys; b=[x for x in json.load(open(sys.argv[1]))['batches'] if x['index']==int(sys.argv[2])][0]; print('\\n'.join(b['region_ids']))" "$PLAN_FILE" "$bi")
  local_n="${#REGIONS[@]}"
  log_info "==== BATCH ${bi} start n=${local_n} ===="
  printf 'batch=%s start n=%s utc=%s\n' "$bi" "$local_n" "$(date -u +%Y%m%dT%H%M%SZ)" >>"$PROGRESS_FILE"

  "${SCRIPT_DIR}/check-disk-quota.sh" || pause_run "disk quota gate failed before batch ${bi}"
  disk_report

  done_in_batch=0
  for rid in "${REGIONS[@]}"; do
    if [[ -f "$STOP_FILE" ]]; then
      pause_run "stop file seen mid-batch ${bi} at region ${rid}"
    fi
    process_region "$rid"
    done_in_batch=$((done_in_batch + 1))
    log_info "batch ${bi} validated ${done_in_batch}/${local_n} region=${rid}"
  done

  log_info "==== BATCH ${bi} all validated — publishing ===="
  for rid in "${REGIONS[@]}"; do
    publish_and_clean_region "$rid"
  done

  elapsed=$(( $(date -u +%s) - START_EPOCH ))
  log_info "==== BATCH ${bi} complete elapsed_s=${elapsed} ===="
  printf 'batch=%s complete elapsed_s=%s utc=%s\n' "$bi" "$elapsed" "$(date -u +%Y%m%dT%H%M%SZ)" >>"$PROGRESS_FILE"
  echo "{\"event\":\"batch_ok\",\"batch\":${bi},\"n\":${local_n},\"elapsed_s\":${elapsed}}" >>"$STATE_FILE"
  disk_report
done

log_info "==== planet-leaves batched bake COMPLETE ===="
echo "{\"event\":\"complete\"}" >>"$STATE_FILE"
disk_report
exit 0
