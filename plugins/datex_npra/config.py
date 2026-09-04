"""Configuration loading. Enable flag is evaluated before credentials/network."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

# Default snapshot endpoints documented by NPRA DATEX II v3.1 HTTP GET API.
DEFAULT_ENDPOINTS: List[str] = [
    "GetSituation",
    "GetTravelTimeData",
    "GetMeasuredWeatherData",
    "GetCCTVSiteTable",
]

DEFAULT_BASE_URL = "https://datex-server-get-v3-1.atlas.vegvesen.no"
DEFAULT_POLL_INTERVAL_SECS = 300
DEFAULT_USER_AGENT = "navi-server-datex/0.1 (contact: replace-me@example.com)"


def _truthy(raw: str | None, default: bool = False) -> bool:
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def is_enabled(environ: dict[str, str] | None = None) -> bool:
    """Return True only when the feature flag is explicitly on.

    This is the hard gate: callers must check this before credentials or HTTP.
    """
    env = environ if environ is not None else os.environ
    return _truthy(env.get("NAVI_DATEX_NPRA_ENABLED"), default=False)


@dataclass(frozen=True)
class Config:
    enabled: bool
    base_url: str
    endpoints: List[str]
    poll_interval_secs: int
    use_if_modified_since: bool
    user_agent: str
    pack_root: Path
    secrets_file: Path
    cache_dir: Path
    publish_dir: Path
    state_dir: Path


def load_config(
    environ: dict[str, str] | None = None,
    *,
    pack_root: Path | None = None,
) -> Config:
    env = environ if environ is not None else os.environ
    enabled = is_enabled(env)

    root = pack_root
    if root is None:
        root = Path(env.get("NAVI_PACK_ROOT", "/media/navi/navi-server/data"))

    endpoints_raw = env.get("NAVI_DATEX_NPRA_ENDPOINTS", ",".join(DEFAULT_ENDPOINTS))
    endpoints = [e.strip() for e in endpoints_raw.split(",") if e.strip()]
    if not endpoints:
        endpoints = list(DEFAULT_ENDPOINTS)

    try:
        poll = int(env.get("NAVI_DATEX_NPRA_POLL_INTERVAL_SECS", str(DEFAULT_POLL_INTERVAL_SECS)))
    except ValueError:
        poll = DEFAULT_POLL_INTERVAL_SECS
    if poll < 60:
        poll = 60

    secrets = Path(
        env.get(
            "NAVI_DATEX_NPRA_SECRETS_FILE",
            str(root / "secrets" / "datex_npra.env"),
        )
    )

    return Config(
        enabled=enabled,
        base_url=env.get("NAVI_DATEX_NPRA_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        endpoints=endpoints,
        poll_interval_secs=poll,
        use_if_modified_since=_truthy(
            env.get("NAVI_DATEX_NPRA_USE_IF_MODIFIED_SINCE"), default=True
        ),
        user_agent=env.get("NAVI_DATEX_NPRA_USER_AGENT", DEFAULT_USER_AGENT),
        pack_root=root,
        secrets_file=secrets,
        cache_dir=root / "datex_npra" / "cache",
        publish_dir=root / "published" / "datex",
        state_dir=root / "datex_npra" / "state",
    )
