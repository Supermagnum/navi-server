"""Config for the RadioID mirror. No network. No secrets in this module."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from plugins.datex_common.enable import is_truthy
from plugins.repeaterbook.config import DEFAULT_COUNTRY_SLUGS, _parse_slugs, _split_csv, build_user_agent

DEFAULT_API_BASE = "https://radioid.net/api"
DEFAULT_MAP_URL = "https://database.radioid.net/static/map.json"
DEFAULT_COUNTRIES = ("Norway",)
DEFAULT_KEEP_GENERATIONS = 2
DEFAULT_MAX_RETRIES = 4
DEFAULT_PER_PAGE = 200
DEFAULT_PAGE_DELAY_SECS = 0.25


@dataclass(frozen=True)
class Config:
    enabled: bool
    permission_confirmed: bool
    pack_root: Path
    api_base: str
    map_url: str
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
    per_page: int
    page_delay_secs: float
    count_ratio_min: float
    count_ratio_max: float
    request_timeout_secs: float = 60.0


def is_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return is_truthy(env.get("NAVI_RADIOID_ENABLED"), default=False)


def permission_confirmed(environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return is_truthy(env.get("RADIOID_PERMISSION_CONFIRMED"), default=False)


def load_config(environ: Mapping[str, str] | None = None) -> Config:
    env = environ if environ is not None else os.environ
    pack_root = Path(env.get("NAVI_PACK_ROOT", "/media/navi/navi-server/data")).expanduser()
    countries = _split_csv(env.get("NAVI_RADIOID_COUNTRIES")) or DEFAULT_COUNTRIES
    app_name = (env.get("NAVI_RADIOID_APP_NAME") or env.get("NAVI_REPEATERBOOK_APP_NAME") or "").strip()
    app_version = (
        env.get("NAVI_RADIOID_APP_VERSION") or env.get("NAVI_REPEATERBOOK_APP_VERSION") or ""
    ).strip()
    contact = (env.get("NAVI_RADIOID_CONTACT") or env.get("NAVI_REPEATERBOOK_CONTACT") or "").strip()
    ua_override = (env.get("NAVI_RADIOID_USER_AGENT") or "").strip()
    user_agent = ua_override or build_user_agent(
        app_name or "navi-server-radioid",
        app_version or "0.1",
        contact or "REPLACE_WITH_APPROVED_CONTACT",
    )
    secrets = Path(
        env.get(
            "NAVI_RADIOID_SECRETS_FILE",
            str(pack_root / "secrets" / "radioid.env"),
        )
    ).expanduser()
    keep = int(env.get("NAVI_RADIOID_KEEP_GENERATIONS", str(DEFAULT_KEEP_GENERATIONS)))
    if keep < 1:
        keep = 1
    max_retries = int(env.get("NAVI_RADIOID_MAX_RETRIES", str(DEFAULT_MAX_RETRIES)))
    if max_retries < 0:
        max_retries = 0
    per_page = int(env.get("NAVI_RADIOID_PER_PAGE", str(DEFAULT_PER_PAGE)))
    if per_page < 1:
        per_page = 1
    if per_page > 200:
        per_page = 200
    return Config(
        enabled=is_enabled(env),
        permission_confirmed=permission_confirmed(env),
        pack_root=pack_root,
        api_base=(env.get("NAVI_RADIOID_API_BASE") or DEFAULT_API_BASE).rstrip("/"),
        map_url=(env.get("NAVI_RADIOID_MAP_URL") or DEFAULT_MAP_URL).strip(),
        countries=countries,
        country_slugs=_parse_slugs(env.get("NAVI_RADIOID_COUNTRY_SLUGS"), countries),
        app_name=app_name,
        app_version=app_version,
        contact=contact,
        user_agent=user_agent,
        secrets_file=secrets,
        work_dir=pack_root / "radioid",
        publish_root=pack_root / "published" / "repeaters",
        keep_generations=keep,
        max_retries=max_retries,
        per_page=per_page,
        page_delay_secs=float(
            env.get("NAVI_RADIOID_PAGE_DELAY_SECS", str(DEFAULT_PAGE_DELAY_SECS))
        ),
        count_ratio_min=float(env.get("NAVI_RADIOID_COUNT_RATIO_MIN", "0.25")),
        count_ratio_max=float(env.get("NAVI_RADIOID_COUNT_RATIO_MAX", "4.0")),
        request_timeout_secs=float(env.get("NAVI_RADIOID_TIMEOUT_SECS", "60")),
    )
