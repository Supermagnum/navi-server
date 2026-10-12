"""Config for the RepeaterBook mirror. No network. No secrets in this module."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from plugins.datex_common.enable import is_truthy

DEFAULT_BASE_URL = "https://www.repeaterbook.com/api"
DEFAULT_COUNTRIES = ("Norway",)
DEFAULT_KEEP_GENERATIONS = 2
DEFAULT_MAX_RETRIES = 4
DEFAULT_COUNT_RATIO_MIN = 0.25
DEFAULT_COUNT_RATIO_MAX = 4.0

# RepeaterBook country query name → URL slug under /repeaters/<slug>/
DEFAULT_COUNTRY_SLUGS = {
    "Norway": "no",
    "Sweden": "se",
    "Denmark": "dk",
    "Finland": "fi",
    "Germany": "de",
    "Switzerland": "ch",
}


@dataclass(frozen=True)
class Config:
    enabled: bool
    permission_confirmed: bool
    pack_root: Path
    base_url: str
    countries: tuple[str, ...]
    country_slugs: dict[str, str]
    app_name: str
    app_version: str
    contact: str
    user_agent: str
    secrets_file: Path
    work_dir: Path
    publish_root: Path
    keep_generations: int
    max_retries: int
    count_ratio_min: float
    count_ratio_max: float
    request_timeout_secs: float = 60.0


def _split_csv(raw: str | None) -> tuple[str, ...]:
    if not raw or not raw.strip():
        return ()
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def _parse_slugs(raw: str | None, countries: Sequence[str]) -> dict[str, str]:
    out = dict(DEFAULT_COUNTRY_SLUGS)
    if raw:
        for part in raw.split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            name, _, slug = part.partition(":")
            name = name.strip()
            slug = slug.strip().lower()
            if name and slug and "/" not in slug and slug not in (".", ".."):
                out[name] = slug
    for c in countries:
        if c not in out:
            # Fallback: lowercase alphanumeric slug from the query name.
            slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in c).strip("_")
            out[c] = slug or "unknown"
    return out


def build_user_agent(app_name: str, app_version: str, contact: str) -> str:
    """Build the UA string that must match RepeaterBook approval."""
    name = (app_name or "").strip() or "navi-server-repeaterbook"
    ver = (app_version or "").strip() or "0.1"
    contact_s = (contact or "").strip() or "REPLACE_WITH_APPROVED_CONTACT"
    return f"{name}/{ver} (+https://github.com/Supermagnum/navi-server; {contact_s})"


def is_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return is_truthy(env.get("NAVI_REPEATERBOOK_ENABLED"), default=False)


def permission_confirmed(environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return is_truthy(env.get("REPEATERBOOK_PERMISSION_CONFIRMED"), default=False)


def load_config(environ: Mapping[str, str] | None = None) -> Config:
    env = environ if environ is not None else os.environ
    pack_root = Path(
        env.get("NAVI_PACK_ROOT", "/media/navi/navi-server/data")
    ).expanduser()
    countries = _split_csv(env.get("NAVI_REPEATERBOOK_COUNTRIES")) or DEFAULT_COUNTRIES
    app_name = (env.get("NAVI_REPEATERBOOK_APP_NAME") or "").strip()
    app_version = (env.get("NAVI_REPEATERBOOK_APP_VERSION") or "").strip()
    contact = (env.get("NAVI_REPEATERBOOK_CONTACT") or "").strip()
    ua_override = (env.get("NAVI_REPEATERBOOK_USER_AGENT") or "").strip()
    user_agent = ua_override or build_user_agent(app_name, app_version, contact)

    secrets = Path(
        env.get(
            "NAVI_REPEATERBOOK_SECRETS_FILE",
            str(pack_root / "secrets" / "repeaterbook.env"),
        )
    ).expanduser()

    keep = int(env.get("NAVI_REPEATERBOOK_KEEP_GENERATIONS", str(DEFAULT_KEEP_GENERATIONS)))
    if keep < 1:
        keep = 1
    max_retries = int(env.get("NAVI_REPEATERBOOK_MAX_RETRIES", str(DEFAULT_MAX_RETRIES)))
    if max_retries < 0:
        max_retries = 0

    return Config(
        enabled=is_enabled(env),
        permission_confirmed=permission_confirmed(env),
        pack_root=pack_root,
        base_url=(env.get("NAVI_REPEATERBOOK_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
        countries=countries,
        country_slugs=_parse_slugs(env.get("NAVI_REPEATERBOOK_COUNTRY_SLUGS"), countries),
        app_name=app_name,
        app_version=app_version,
        contact=contact,
        user_agent=user_agent,
        secrets_file=secrets,
        work_dir=pack_root / "repeaterbook",
        publish_root=pack_root / "published" / "repeaters",
        keep_generations=keep,
        max_retries=max_retries,
        count_ratio_min=float(
            env.get("NAVI_REPEATERBOOK_COUNT_RATIO_MIN", str(DEFAULT_COUNT_RATIO_MIN))
        ),
        count_ratio_max=float(
            env.get("NAVI_REPEATERBOOK_COUNT_RATIO_MAX", str(DEFAULT_COUNT_RATIO_MAX))
        ),
        request_timeout_secs=float(env.get("NAVI_REPEATERBOOK_TIMEOUT_SECS", "60")),
    )
