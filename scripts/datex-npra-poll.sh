#!/usr/bin/env bash
# Compatibility wrapper for navi-datex-npra.service.
# Polls all enabled DATEX providers (toml). Vegvesen remains gated by
# NAVI_DATEX_NPRA_ENABLED via config/datex/providers/no-vegvesen.toml.
#
# Usage:
#   ./datex-npra-poll.sh
#   ./datex-npra-poll.sh --no-jitter

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --no-jitter kept for legacy callers; maps to default poll (jitter on).
FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-jitter) shift ;;
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

if [[ "$FORCE" -eq 1 ]]; then
  exec "${SCRIPT_DIR}/datex-poll-all.sh" --force
fi
exec "${SCRIPT_DIR}/datex-poll-all.sh"
