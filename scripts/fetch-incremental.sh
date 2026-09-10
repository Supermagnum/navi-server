#!/usr/bin/env bash
# Prefer Geofabrik .osc.gz incremental update of a held PBF when possible.
#
# Decision (single place for update-vs-full):
#   1. Region source must be geofabrik:* (OSM.fr / url: → full fetch).
#   2. A previously published pack must exist for the region (not first bake).
#   3. A held PBF with osmosis_replication_* headers must exist at
#      region_pbf_path (or --held-pbf). If published but no held PBF → full.
#   4. Held replication state must be within NAVI_GEOFABRIK_DIFF_RETENTION_DAYS
#      (~100) of the tip; otherwise log fallback and exit 10.
#   5. Otherwise apply diffs in place (or to --output) via
#      lib/geofabrik_replication.py (osmium-tool + urllib; no pyosmium).
#
# Exit codes:
#   0  — updated or already current
#  10  — incremental unavailable → caller should full-fetch
#   2  — hard error
#
# Usage:
#   ./fetch-incremental.sh us_west_virginia
#   ./fetch-incremental.sh --held-pbf /path/in.osm.pbf --output /path/out.osm.pbf us_west_virginia
#
# NOT wired into planet-leaves by default. Weekly bake enables this via
# fetch-extracts.sh --prefer-incremental from run-weekly.sh.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config
require_cmd osmium python3

: "${NAVI_GEOFABRIK_DIFF_RETENTION_DAYS:=100}"

HELD_PBF=""
OUTPUT_PBF=""
REGION_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --held-pbf) HELD_PBF="$2"; shift 2 ;;
    --output) OUTPUT_PBF="$2"; shift 2 ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *)
      [[ -z "$REGION_ID" ]] || die "unexpected arg: $1"
      REGION_ID="$1"
      shift
      ;;
  esac
done

[[ -n "$REGION_ID" ]] || die "usage: fetch-incremental.sh [--held-pbf PATH] [--output PATH] <region_id>"

src="$(region_source "$REGION_ID" || true)"
[[ -n "$src" ]] || die "unknown region id=${REGION_ID}"

case "$src" in
  geofabrik:*)
    ;;
  *)
    log_info "incremental unavailable region=${REGION_ID} reason=non-geofabrik source=${src}"
    exit 10
    ;;
esac

if ! region_is_published "$REGION_ID"; then
  log_info "incremental unavailable region=${REGION_ID} reason=no prior published pack (first-ever bake → full fetch)"
  exit 10
fi

held="${HELD_PBF:-$(region_pbf_path "$REGION_ID")}"
out="${OUTPUT_PBF:-$held}"

if [[ ! -f "$held" ]]; then
  log_info "incremental unavailable region=${REGION_ID} reason=no held PBF at ${held} (published pack exists but extract was not retained → full fetch)"
  exit 10
fi

log_info "incremental attempt region=${REGION_ID} held=${held} retention_days=${NAVI_GEOFABRIK_DIFF_RETENTION_DAYS}"

tmp="${out}.incremental.partial.osm.pbf"
json_out="$(mktemp)"
rm -f "$tmp"
set +e
python3 "${SCRIPT_DIR}/lib/geofabrik_replication.py" \
  --retention-days "$NAVI_GEOFABRIK_DIFF_RETENTION_DAYS" \
  --json \
  -o "$tmp" \
  "$held" >"$json_out"
rc=$?
set -e

if [[ "$rc" -eq 10 ]]; then
  rm -f "$tmp" "$json_out"
  log_info "incremental unavailable, falling back to full region=${REGION_ID} (see geofabrik_replication reason above)"
  exit 10
fi
if [[ "$rc" -ne 0 ]]; then
  rm -f "$tmp" "$json_out"
  die "incremental apply failed region=${REGION_ID} rc=${rc}"
fi

if [[ ! -f "$tmp" ]]; then
  rm -f "$json_out"
  die "incremental produced no output region=${REGION_ID}"
fi

# Re-verify osmosis_replication_sequence_number on the temp PBF matches the
# tip reported by the apply step — refuse atomic replace on any mismatch.
tip_seq="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1],encoding="utf-8")); s=d.get("tip_sequence"); assert s is not None, "missing tip_sequence in apply JSON"; print(int(s))' "$json_out")"
got_seq="$(osmium fileinfo --input-format=pbf -j "$tmp" | python3 -c 'import json,sys; o=json.load(sys.stdin).get("header",{}).get("option",{}); s=o.get("osmosis_replication_sequence_number"); assert s is not None, "missing osmosis_replication_sequence_number on temp PBF"; print(int(s))')"
if [[ "$got_seq" != "$tip_seq" ]]; then
  rm -f "$tmp" "$json_out"
  log_fail "SEQUENCE_TIP_VERIFY=FAIL region=${REGION_ID} got_seq=${got_seq} expected_tip=${tip_seq} — held PBF not replaced"
  die "SEQUENCE_TIP_MISMATCH region=${REGION_ID} got_seq=${got_seq} expected_tip=${tip_seq}"
fi
log_info "SEQUENCE_TIP_VERIFY=PASS region=${REGION_ID} seq=${got_seq} tip=${tip_seq}"
rm -f "$json_out"

# Atomic replace when updating in place or to --output.
mkdir -p "$(dirname "$out")"
mv "$tmp" "$out"
# Drop stale md5 — updated PBF no longer matches Geofabrik -latest.md5.
rm -f "${out}.md5"
log_info "incremental OK region=${REGION_ID} out=${out}"
exit 0
