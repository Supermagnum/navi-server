#!/usr/bin/env bash
# Operator CLI for DATEX providers (add/test/list/enable/…).
# Prefer: sudo ./scripts/datex-provider.sh … when writing /etc/navi-server/datex/.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAVI_SERVER_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
export PYTHONPATH="${NAVI_SERVER_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export NAVI_DATEX_PROVIDERS_DIR="${NAVI_DATEX_PROVIDERS_DIR:-${NAVI_SERVER_ROOT}/config/datex/providers}"
exec python3 -m plugins.datex_provider "$@"
