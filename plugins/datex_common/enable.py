"""Enable / truthy helpers. Disabled providers must not read secrets or network."""

from __future__ import annotations

import os
from typing import Mapping, Optional


def is_truthy(raw: str | None, default: bool = False) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def provider_enabled(
    provider_id: str,
    environ: Mapping[str, str] | None = None,
    *,
    env_key: Optional[str] = None,
) -> bool:
    """Return True only when NAVI_DATEX_<ID>_ENABLED (or env_key) is explicitly on.

    Convention: provider_id ``npra`` → ``NAVI_DATEX_NPRA_ENABLED``.
    """
    env = environ if environ is not None else os.environ
    key = env_key or f"NAVI_DATEX_{provider_id.strip().upper()}_ENABLED"
    return is_truthy(env.get(key), default=False)
