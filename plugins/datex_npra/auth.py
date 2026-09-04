"""Credential loading and operator-facing auth failure messages.

Never put credentials in argv. Never log credential values or Authorization.
Interactive prompts are intentionally unsupported (server daemon / setup owns that).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Tuple

# Operator-facing messages (journal / poller stderr). Not returned on the public
# static GET surface. Exact live NPRA 401/403 *body* shapes are UNVERIFIED.
MSG_MISSING_CREDENTIALS = (
    "DATEX NPRA: credentials not configured. Set NAV_DATEX_USERNAME and "
    "NAV_DATEX_PASSWORD, or provide a mode-0600 secrets file "
    "(NAVI_DATEX_NPRA_SECRETS_FILE)."
)
MSG_HTTP_401 = (
    "DATEX NPRA: authentication failed (HTTP 401 Unauthorized). "
    "Check NAV_DATEX_USERNAME / NAV_DATEX_PASSWORD."
)
MSG_HTTP_403 = (
    "DATEX NPRA: access forbidden (HTTP 403 Forbidden). "
    "Account may lack permission for this endpoint, or the client IP/DNS "
    "is not allowlisted by NPRA."
)


class AuthError(RuntimeError):
    """Fail-closed credential / auth error for operators (not clients)."""


def auth_failure_message(kind: str) -> str:
    """Return the canonical operator message for missing / 401 / 403."""
    key = kind.strip().lower()
    if key in ("missing", "missing_credentials", "no_credentials"):
        return MSG_MISSING_CREDENTIALS
    if key in ("401", "unauthorized"):
        return MSG_HTTP_401
    if key in ("403", "forbidden"):
        return MSG_HTTP_403
    raise ValueError(f"unknown auth failure kind: {kind}")


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


def load_credentials(
    *,
    environ: Mapping[str, str] | None = None,
    secrets_file: Path | None = None,
) -> Credentials:
    """Load credentials: env first, then secrets file. Never prompts."""
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

    raise AuthError(auth_failure_message("missing"))
