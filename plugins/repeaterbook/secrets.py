"""Load RepeaterBook server app_ token. Never log the token value."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

TOKEN_REDACT = "[REDACTED_REPEATERBOOK_TOKEN]"

# Server/backend shared application tokens only.
_APP_TOKEN_RE = re.compile(r"^app_[A-Za-z0-9._\-]+$")


class AuthError(RuntimeError):
    """Fail-closed credential error for operators (never for clients)."""


@dataclass(frozen=True)
class AppToken:
    token: str

    def __repr__(self) -> str:
        return f"AppToken(token={TOKEN_REDACT!r})"


def redact_secrets(text: str, token: str | None) -> str:
    """Remove token substrings from error/log text."""
    if not text:
        return text
    out = text
    if token:
        out = out.replace(token, TOKEN_REDACT)
    # Belt-and-braces for any app_/rbuapp_ looking fragments in messages.
    out = re.sub(r"\b(?:app_|rbuapp_)[A-Za-z0-9._\-]{8,}\b", TOKEN_REDACT, out)
    return out


def validate_app_token(raw: str) -> str:
    """Accept only centralized ``app_…`` tokens. Reject ``rbuapp_…``."""
    token = (raw or "").strip()
    if not token:
        raise AuthError(
            "RepeaterBook: app token not configured. Set REPEATERBOOK_APP_TOKEN "
            "in the secrets file (mode 0600) to an approved server/backend app_ token."
        )
    if token.startswith("rbuapp_"):
        raise AuthError(
            "RepeaterBook: rbuapp_ (per-user distributed) tokens are not allowed. "
            "Use a server/backend app_ token issued for this host only."
        )
    if not _APP_TOKEN_RE.match(token):
        raise AuthError(
            "RepeaterBook: token must be an approved server/backend app_ token "
            "(prefix app_). Regenerated tokens that do not match are rejected."
        )
    return token


def _parse_secrets_file(path: Path) -> Optional[str]:
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
        if key in (
            "REPEATERBOOK_APP_TOKEN",
            "NAVI_REPEATERBOOK_APP_TOKEN",
            "APP_TOKEN",
        ):
            return val
    return None


def assert_secrets_mode(path: Path) -> None:
    """Refuse world/group-readable secrets files."""
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise AuthError(
            f"RepeaterBook: secrets file {path} mode is {mode:04o}; "
            "refuse to load (require 0600, no group/other access)."
        )


def load_app_token(
    *,
    environ: Mapping[str, str] | None = None,
    secrets_file: Path | None = None,
) -> AppToken:
    """Load token from env first, then secrets file. Never prompts."""
    env = environ if environ is not None else os.environ
    raw = (env.get("REPEATERBOOK_APP_TOKEN") or env.get("NAVI_REPEATERBOOK_APP_TOKEN") or "").strip()
    if not raw and secrets_file is not None and secrets_file.is_file():
        assert_secrets_mode(secrets_file)
        file_tok = _parse_secrets_file(secrets_file)
        raw = (file_tok or "").strip()
    return AppToken(token=validate_app_token(raw))
