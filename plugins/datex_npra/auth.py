"""Credential loading and operator-facing auth failure messages.

Never put credentials in argv. Never log credential values or Authorization.
Interactive prompts are intentionally unsupported (server daemon / setup owns that).
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from plugins.datex_common.secrets import AuthError, Credentials, load_basic_credentials

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


def load_credentials(
    *,
    environ: Mapping[str, str] | None = None,
    secrets_file: Path | None = None,
) -> Credentials:
    """Load credentials: env first, then secrets file. Never prompts."""
    return load_basic_credentials(
        environ=environ,
        secrets_file=secrets_file,
        missing_message=auth_failure_message("missing"),
    )


__all__ = [
    "AuthError",
    "Credentials",
    "MSG_HTTP_401",
    "MSG_HTTP_403",
    "MSG_MISSING_CREDENTIALS",
    "auth_failure_message",
    "load_credentials",
]
