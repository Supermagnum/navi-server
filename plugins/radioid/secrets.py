"""Optional RadioID API token. Never log the token value."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

TOKEN_REDACT = "[REDACTED_RADIOID_TOKEN]"


class AuthError(RuntimeError):
    pass


@dataclass(frozen=True)
class OptionalToken:
    token: Optional[str]

    def __repr__(self) -> str:
        if self.token is None:
            return "OptionalToken(token=None)"
        return f"OptionalToken(token={TOKEN_REDACT!r})"


def redact_secrets(text: str, token: str | None) -> str:
    if not text:
        return text
    out = text
    if token:
        out = out.replace(token, TOKEN_REDACT)
    return out


def assert_secrets_mode(path: Path) -> None:
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise AuthError(
            f"RadioID: secrets file {path} mode is {mode:04o}; "
            "refuse to load (require 0600, no group/other access)."
        )


def _parse_secrets_file(path: Path) -> Optional[str]:
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key in ("RADIOID_API_TOKEN", "NAVI_RADIOID_API_TOKEN", "X_API_TOKEN"):
            return val
    return None


def load_optional_token(
    *,
    environ: Mapping[str, str] | None = None,
    secrets_file: Path | None = None,
) -> OptionalToken:
    """Load optional token from env or secrets file. Missing file → anonymous."""
    env = environ if environ is not None else os.environ
    raw = (env.get("RADIOID_API_TOKEN") or env.get("NAVI_RADIOID_API_TOKEN") or "").strip()
    if not raw and secrets_file is not None and secrets_file.is_file():
        assert_secrets_mode(secrets_file)
        file_tok = _parse_secrets_file(secrets_file)
        raw = (file_tok or "").strip()
    if not raw:
        return OptionalToken(token=None)
    return OptionalToken(token=raw)
