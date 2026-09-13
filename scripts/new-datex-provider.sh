#!/usr/bin/env bash
# Create a minimal disabled DATEX provider stub under plugins/datex_<id>/.
# Does not enable polling, install systemd units, or add endpoints.
#
# Usage:
#   ./scripts/new-datex-provider.sh <provider_id>
# Example:
#   ./scripts/new-datex-provider.sh flanders

set -euo pipefail

NAVI_SERVER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
id_raw="${1:-}"
if [[ -z "$id_raw" ]]; then
  echo "usage: $0 <provider_id>" >&2
  exit 1
fi
if [[ ! "$id_raw" =~ ^[a-z][a-z0-9_]{0,31}$ ]]; then
  echo "provider_id must match ^[a-z][a-z0-9_]{0,31}$" >&2
  exit 1
fi
if [[ "$id_raw" == "common" || "$id_raw" == "npra" ]]; then
  echo "refusing to overwrite reserved id: ${id_raw}" >&2
  exit 1
fi

pkg="datex_${id_raw}"
dest="${NAVI_SERVER_ROOT}/plugins/${pkg}"
if [[ -e "$dest" ]]; then
  echo "already exists: ${dest}" >&2
  exit 1
fi

id_upper="$(printf '%s' "$id_raw" | tr '[:lower:]' '[:upper:]')"
mkdir -p "${dest}/tests"

cat >"${dest}/__init__.py" <<EOF
"""DATEX provider stub for ${id_raw} — disabled by default; not implemented."""

PROVIDER_ID = "${id_raw}"
EOF

cat >"${dest}/config.py" <<EOF
"""Config stub. Enable only via NAVI_DATEX_${id_upper}_ENABLED=1 after implementation."""

from __future__ import annotations

from pathlib import Path

from plugins.datex_common.enable import provider_enabled
from plugins.datex_common.publish import provider_publish_dir

PROVIDER_ID = "${id_raw}"


def is_enabled(environ=None) -> bool:
    return provider_enabled(PROVIDER_ID, environ)


def publish_dir(pack_root: Path) -> Path:
    return provider_publish_dir(pack_root, PROVIDER_ID)
EOF

cat >"${dest}/poll.py" <<EOF
"""Poll stub: enable gate only. Implement fetch/publish before enabling."""

from __future__ import annotations

import logging
import os
import sys

from .config import is_enabled

LOG = logging.getLogger("navi.datex_${id_raw}")


def poll_once(*, environ=None) -> int:
    env = environ if environ is not None else os.environ
    if not is_enabled(env):
        LOG.info("datex_${id_raw} disabled (NAVI_DATEX_${id_upper}_ENABLED!=1); inert exit")
        return 0
    LOG.error("datex_${id_raw}: enabled but poller not implemented — failing closed")
    return 2


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    return poll_once()


if __name__ == "__main__":
    sys.exit(main())
EOF

cat >"${dest}/__main__.py" <<EOF
from .poll import main

raise SystemExit(main())
EOF

cat >"${dest}/tests/test_stub.py" <<EOF
import unittest
from plugins.${pkg}.config import is_enabled


class StubTests(unittest.TestCase):
    def test_disabled_by_default(self):
        self.assertFalse(is_enabled({}))


if __name__ == "__main__":
    unittest.main()
EOF

echo "created ${dest} (disabled stub)"
echo "Next: implement client.py, document NAVI_DATEX_${id_upper}_ENABLED in config.example.env,"
echo "add providers_index entry, systemd units, and docs — keep ENABLED=0 until ready."
