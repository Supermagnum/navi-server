#!/usr/bin/env bash
# Poll all enabled DATEX providers from config/datex/providers/*.toml.
# One provider failure does not stop the others.
#
# Usage:
#   ./datex-poll-all.sh
#   ./datex-poll-all.sh --force          # ignore interval holds
#   ./datex-poll-all.sh --only no-vegvesen

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAVI_SERVER_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

export NAVI_PACK_ROOT="${NAVI_PACK_ROOT}"
export PYTHONPATH="${NAVI_SERVER_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export NAVI_DATEX_PROVIDERS_DIR="${NAVI_DATEX_PROVIDERS_DIR:-${NAVI_SERVER_ROOT}/config/datex/providers}"

ONLY=""
FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --only) ONLY="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

args=()
[[ "$FORCE" -eq 1 ]] && args+=(--force)
[[ -n "$ONLY" ]] && args+=("$ONLY")

exec python3 -m plugins.datex_provider poll "${args[@]+"${args[@]}"}"
