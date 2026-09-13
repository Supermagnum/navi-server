"""Credential loading for DATEX providers. Never log secret values."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple


class AuthError(RuntimeError):
    """Fail-closed credential / auth error for operators (not clients)."""


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


def _parse_secrets_file(path: Path) -> Tuple[Optional[str], Optional[str]]:
    user = None
    password = None
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key in ("NAV_DATEX_USERNAME", "USERNAME", "username"):
            user = val
        elif key in ("NAV_DATEX_PASSWORD", "PASSWORD", "password"):
            password = val
    return user, password


def load_basic_credentials(
    *,
    environ: Mapping[str, str] | None = None,
    secrets_file: Path | None = None,
    missing_message: str = (
        "DATEX: credentials not configured. Set NAV_DATEX_USERNAME and "
        "NAV_DATEX_PASSWORD, or provide a mode-0600 secrets file."
    ),
) -> Credentials:
    """Load Basic-auth credentials: process env first, then secrets file.

    Never prompts. Does not log username/password values.
    """
    env = environ if environ is not None else os.environ
    user = (env.get("NAV_DATEX_USERNAME") or "").strip()
    password = env.get("NAV_DATEX_PASSWORD")
    if password is not None:
        password = password.strip("\n\r")

    if user and password is not None and password != "":
        return Credentials(username=user, password=password)

    if secrets_file is not None and secrets_file.is_file():
        file_user, file_pass = _parse_secrets_file(secrets_file)
        user = user or (file_user or "")
        if password is None or password == "":
            password = file_pass
        if user and password is not None and password != "":
            return Credentials(username=user, password=password)

    raise AuthError(missing_message)
