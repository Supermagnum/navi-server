#!/usr/bin/env bash
# Regression: sun-order must not capture log lines into region id lists.
#
# Exercises: sun-order enabled, .bboxes.json absent, multiple region ids.
# Asserts ORDERED_IDS are only real ids and SRC_BY_ID lookups succeed.
#
# Usage: ./test-sun-order-no-log-pollution.sh
# Expected: PASS (exit 0). Against pre-fix common.sh this fails.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

CONF="${TMP}/regions.conf"
# No sibling ${CONF}.bboxes.json — forces the missing-bbox WARN path.
cat >"$CONF" <<'EOF'
# fixture for sun-order log-pollution regression
alpha	geofabrik:europe/andorra
beta	geofabrik:europe/albania
EOF

export NAVI_REGIONS_CONF="$CONF"
unset NAVI_BAKE_SUN_ORDER || true
# Default sun-order on.

WORK=()
while IFS=$'\t' read -r region_id src; do
  WORK+=("${region_id}"$'\t'"${src}")
done < <(list_regions)

[[ ${#WORK[@]} -eq 2 ]] || {
  echo "FAIL: expected 2 regions from fixture, got ${#WORK[@]}" >&2
  exit 1
}

# Same pipeline shape as fetch-extracts.sh full-set reorder.
mapfile -t ORDERED_IDS < <(
  printf '%s\n' "${WORK[@]}" | awk -F'\t' '{print $1}' | follow_sun_order_ids
)

declare -A SRC_BY_ID=()
for row in "${WORK[@]}"; do
  SRC_BY_ID["${row%%$'\t'*}"]="${row#*$'\t'}"
done

if [[ ${#ORDERED_IDS[@]} -ne 2 ]]; then
  echo "FAIL: ORDERED_IDS count=${#ORDERED_IDS[@]} want=2 (log pollution?)" >&2
  printf '  id=%q\n' "${ORDERED_IDS[@]}" >&2
  exit 1
fi

for region_id in "${ORDERED_IDS[@]}"; do
  if [[ "$region_id" == *"["* ]] || [[ "$region_id" == *"WARN"* ]] || [[ "$region_id" == *"sun-order"* ]]; then
    echo "FAIL: ORDERED_IDS contains log noise: ${region_id@Q}" >&2
    exit 1
  fi
  if [[ ! -v SRC_BY_ID[$region_id] ]]; then
    echo "FAIL: SRC_BY_ID missing key ${region_id@Q}" >&2
    exit 1
  fi
  # Must succeed under set -u (this script already has set -u).
  _="${SRC_BY_ID[$region_id]}"
done

# convert-region path: order_regions_array_follow_sun must keep only real ids.
IDS=(alpha beta)
order_regions_array_follow_sun IDS
if [[ ${#IDS[@]} -ne 2 ]]; then
  echo "FAIL: order_regions_array_follow_sun count=${#IDS[@]} want=2" >&2
  printf '  id=%q\n' "${IDS[@]}" >&2
  exit 1
fi
for region_id in "${IDS[@]}"; do
  [[ "$region_id" == "alpha" || "$region_id" == "beta" ]] || {
    echo "FAIL: unexpected id after order_regions_array_follow_sun: ${region_id@Q}" >&2
    exit 1
  }
done

echo "PASS: sun-order without bboxes keeps real ids only (no log pollution)"
