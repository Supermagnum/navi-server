"""Configuration loading. Enable flag is evaluated before credentials/network."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

# Default snapshot endpoints documented by NPRA DATEX II v3.1 HTTP GET API.
DEFAULT_ENDPOINTS: List[str] = [
    "GetSituation",
    "GetTravelTimeData",
    "GetMeasuredWeatherData",
    "GetCCTVSiteTable",
]

DEFAULT_BASE_URL = "https://datex-server-get-v3-1.atlas.vegvesen.no"
# Fallback when an endpoint is absent from the per-endpoint map.
DEFAULT_POLL_INTERVAL_SECS = 300
DEFAULT_USER_AGENT = "navi-server-datex/0.1 (contact: replace-me@example.com)"

# NPRA-aligned refresh cadences (seconds). Systemd timer still fires ~every
# 5 min; endpoints with a longer interval set next_attempt_unix and skip
# upstream GET until due.
#
# GetCCTVSiteTable is camera *site metadata* (not live images). 12h (43200)
# keeps the published snapshot same-day fresh while cutting ~286 of ~288
# daily pulls vs a 5 min cadence. Prefer 6h for faster camera-list updates;
# 24h is acceptable for near-static inventories.
DEFAULT_ENDPOINT_INTERVALS: Dict[str, int] = {
    "GetSituation": 300,
    "GetTravelTimeData": 300,
    "GetMeasuredWeatherData": 600,
    "GetCCTVSiteTable": 43200,
}


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


def _clamp_interval(raw: int) -> int:
    if raw < 60:
        return 60
    return raw


def _parse_endpoint_intervals(raw: str | None) -> Dict[str, int]:
    """Parse Endpoint=secs[,Endpoint=secs…] overrides; start from defaults."""
    out = dict(DEFAULT_ENDPOINT_INTERVALS)
    if not raw or not raw.strip():
        return out
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, val = part.partition("=")
        name = name.strip()
        val = val.strip()
        if not name:
            continue
        try:
            out[name] = _clamp_interval(int(val))
        except ValueError:
            continue
    return out


@dataclass(frozen=True)
class Config:
    enabled: bool
    base_url: str
    endpoints: List[str]
    poll_interval_secs: int
    endpoint_poll_interval_secs: Dict[str, int] = field(default_factory=dict)
    use_if_modified_since: bool = True
    user_agent: str = DEFAULT_USER_AGENT
    pack_root: Path = Path("/media/navi/navi-server/data")
    secrets_file: Path = Path("/media/navi/navi-server/data/secrets/datex_npra.env")
    cache_dir: Path = Path("/media/navi/navi-server/data/datex_npra/cache")
    publish_dir: Path = Path("/media/navi/navi-server/data/published/datex")
    state_dir: Path = Path("/media/navi/navi-server/data/datex_npra/state")

    def interval_for(self, endpoint: str) -> int:
        """Per-endpoint poll interval; falls back to global poll_interval_secs."""
        mapped = self.endpoint_poll_interval_secs.get(endpoint)
        if mapped is not None:
            return mapped
        return self.poll_interval_secs


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
    poll = _clamp_interval(poll)

    endpoint_intervals = _parse_endpoint_intervals(
        env.get("NAVI_DATEX_NPRA_ENDPOINT_INTERVALS"),
    )

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
        endpoint_poll_interval_secs=endpoint_intervals,
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
