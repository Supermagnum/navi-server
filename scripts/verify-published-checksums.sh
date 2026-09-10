#!/usr/bin/env bash
# Read-only sha256 re-verification of published pack generations.
#
# Checks files listed in each generation's checksums.sha256 (written by
# publish-packs.sh / publish-safe.sh). Never deletes or mutates pack files.
#
# Clear signal for orchestrators / future snapshot-rollback:
#   CHECKSUM_REVERIFY=PASS  — all checked generations matched
#   CHECKSUM_REVERIFY=FAIL  — at least one mismatch / missing digest file
# Non-zero exit on FAIL.
#
# Usage:
#   ./verify-published-checksums.sh
#       # live gen for every region under published/packs (complete_gens[0])
#   ./verify-published-checksums.sh --all-complete
#       # every complete generation still on disk (live + kept priors)
#   ./verify-published-checksums.sh --pack-dir /path/to/packs/rel/GEN
#   ./verify-published-checksums.sh --region europe/andorra
#   ./verify-published-checksums.sh --region europe/andorra --generation 20260904T104909Z

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config
require_cmd sha256sum python3

ALL_COMPLETE=0
PACK_DIRS=()
FILTER_REGION=""
FILTER_GEN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --all-complete) ALL_COMPLETE=1; shift ;;
    --pack-dir) PACK_DIRS+=("$2"); shift 2 ;;
    --region) FILTER_REGION="$2"; shift 2 ;;
    --generation) FILTER_GEN="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,22p' "$0"
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
done

verify_one_pack_dir() {
  local dir="$1"
  local cs="${dir}/checksums.sha256"
  if [[ ! -d "$dir" ]]; then
    log_fail "CHECKSUM_REVERIFY=FAIL path=${dir} reason=not_a_directory"
    return 1
  fi
  if [[ -f "${dir}/.publish_in_progress" ]]; then
    log_fail "CHECKSUM_REVERIFY=FAIL path=${dir} reason=publish_in_progress"
    return 1
  fi
  if [[ ! -f "$cs" ]]; then
    log_fail "CHECKSUM_REVERIFY=FAIL path=${dir} reason=missing_checksums.sha256"
    return 1
  fi
  # sha256sum -c is read-only; run with cwd=dir so relative names resolve.
  local out rc
  set +e
  out="$(cd "$dir" && sha256sum -c checksums.sha256 --strict 2>&1)"
  rc=$?
  set -e
  if [[ "$rc" -ne 0 ]]; then
    log_fail "CHECKSUM_REVERIFY=FAIL path=${dir} reason=sha256_mismatch"
    printf '%s\n' "$out" >&2
    return 1
  fi
  log_info "CHECKSUM_REVERIFY=PASS path=${dir}"
  return 0
}

# Resolve pack dirs when none were passed explicitly.
if [[ ${#PACK_DIRS[@]} -eq 0 ]]; then
  mapfile -t PACK_DIRS < <(
    ALL_COMPLETE="$ALL_COMPLETE" \
    FILTER_REGION="$FILTER_REGION" \
    FILTER_GEN="$FILTER_GEN" \
    NAVI_PUBLISHED_DIR="$NAVI_PUBLISHED_DIR" \
    python3 - "${SCRIPT_DIR}/lib" <<'PY'
import os, sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from published_tree import complete_gens, iter_published_region_dirs, is_generation_dir

published = Path(os.environ["NAVI_PUBLISHED_DIR"])
packs = published / "packs"
all_complete = os.environ.get("ALL_COMPLETE", "0") == "1"
filt_region = (os.environ.get("FILTER_REGION") or "").strip().strip("/")
filt_gen = (os.environ.get("FILTER_GEN") or "").strip()

if not packs.is_dir():
    sys.exit(0)

region_dirs = iter_published_region_dirs(packs)
if filt_region:
    want = packs / filt_region
    region_dirs = [d for d in region_dirs if d == want or d.as_posix().endswith("/" + filt_region)]
    if want.is_dir() and want not in region_dirs:
        # Allow verifying a region path even if it has no complete gens yet
        # (caller will then see missing checksums / empty list).
        region_dirs = [want]

for region_dir in region_dirs:
    gens = complete_gens(region_dir)
    if filt_gen:
        gens = [g for g in gens if g.name == filt_gen]
        if not gens:
            candidate = region_dir / filt_gen
            if candidate.is_dir() and is_generation_dir(filt_gen):
                gens = [candidate]
    elif not all_complete:
        gens = gens[:1]
    for g in gens:
        print(g)
PY
  )
fi

if [[ ${#PACK_DIRS[@]} -eq 0 ]]; then
  log_info "CHECKSUM_REVERIFY=PASS path=none reason=no_pack_dirs_to_check"
  exit 0
fi

fail=0
checked=0
for d in "${PACK_DIRS[@]}"; do
  checked=$((checked + 1))
  if ! verify_one_pack_dir "$d"; then
    fail=$((fail + 1))
  fi
done

if [[ "$fail" -ne 0 ]]; then
  log_fail "CHECKSUM_REVERIFY=FAIL checked=${checked} failed=${fail}"
  exit 1
fi
log_info "CHECKSUM_REVERIFY=PASS checked=${checked} failed=0"
exit 0
