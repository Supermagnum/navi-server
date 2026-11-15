"""Load auth material for DATEX providers. Never log secret values."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional

from .provider_spec import ProviderSpec
from .secrets import AuthError, Credentials, load_basic_credentials


@dataclass(frozen=True)
class AuthMaterial:
    """Opaque auth bundle. Do not put into logs or exception messages."""

    kind: str
    basic: Optional[Credentials] = None
    token: Optional[str] = None  # bearer or api-key value
    # Extra headers derived at apply time only


def _parse_kv_file(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip("'").strip('"')
    return out


def _secrets_candidates(spec: ProviderSpec, environ: Mapping[str, str]) -> list[Path]:
    paths: list[Path] = []
    if spec.secrets_file is not None:
        paths.append(Path(spec.secrets_file))
    # CREDENTIALS_DIRECTORY from systemd LoadCredential=
    cred_dir = (environ.get("CREDENTIALS_DIRECTORY") or "").strip()
    if cred_dir:
        paths.append(Path(cred_dir) / f"{spec.id}.env")
        paths.append(Path(cred_dir) / "datex.env")
    # Legacy NPRA path
    if spec.id == "no-vegvesen" or spec.public_id == "npra":
        legacy = environ.get("NAVI_DATEX_NPRA_SECRETS_FILE", "").strip()
        if legacy:
            paths.append(Path(legacy))
        pack = environ.get("NAVI_PACK_ROOT", "/media/navi/navi-server/data")
        paths.append(Path(pack) / "secrets" / "datex_npra.env")
    paths.append(Path(f"/etc/navi-server/datex/{spec.id}.env"))
    # de-dupe preserving order
    seen = set()
    uniq: list[Path] = []
    for p in paths:
        rp = str(p)
        if rp in seen:
            continue
        seen.add(rp)
        uniq.append(p)
    return uniq


def load_auth_material(
    spec: ProviderSpec,
    *,
    environ: Mapping[str, str] | None = None,
) -> AuthMaterial:
    env = environ if environ is not None else os.environ
    kind = spec.auth_type

    if kind == "none":
        return AuthMaterial(kind="none")

    file_kv: Dict[str, str] = {}
    for path in _secrets_candidates(spec, env):
        if path.is_file():
            try:
                file_kv = _parse_kv_file(path)
                break
            except OSError:
                continue

    if kind == "basic":
        # Prefer env, then file via load_basic_credentials
        secrets_file = None
        for path in _secrets_candidates(spec, env):
            if path.is_file():
                secrets_file = path
                break
        creds = load_basic_credentials(
            environ=env,
            secrets_file=secrets_file,
            missing_message=(
                f"DATEX {spec.id}: credentials not configured for basic auth "
                f"(set NAV_DATEX_USERNAME/NAV_DATEX_PASSWORD or secrets file)."
            ),
        )
        return AuthMaterial(kind="basic", basic=creds)

    if kind == "bearer":
        token = (env.get("NAV_DATEX_BEARER_TOKEN") or env.get("BEARER_TOKEN") or "").strip()
        if not token:
            token = (file_kv.get("NAV_DATEX_BEARER_TOKEN") or file_kv.get("BEARER_TOKEN") or "").strip()
        if not token:
            raise AuthError(
                f"DATEX {spec.id}: bearer token not configured "
                f"(NAV_DATEX_BEARER_TOKEN / BEARER_TOKEN)."
            )
        return AuthMaterial(kind="bearer", token=token)

    if kind == "api-key":
        token = (env.get("NAV_DATEX_API_KEY") or env.get("API_KEY") or "").strip()
        if not token:
            token = (file_kv.get("NAV_DATEX_API_KEY") or file_kv.get("API_KEY") or "").strip()
        if not token:
            raise AuthError(
                f"DATEX {spec.id}: API key not configured (NAV_DATEX_API_KEY / API_KEY)."
            )
        return AuthMaterial(kind="api-key", token=token)

    raise AuthError(f"DATEX {spec.id}: unsupported auth_type {kind!r}")


def apply_auth_headers(
    spec: ProviderSpec,
    auth: AuthMaterial,
    headers: Dict[str, str],
    url: str,
) -> tuple[str, Dict[str, str]]:
    """Return (url, headers) with auth applied. Never returns secret in url for basic/bearer."""
    import base64
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    out_headers = dict(headers)
    out_url = url

    if auth.kind == "none":
        return out_url, out_headers

    if auth.kind == "basic" and auth.basic is not None:
        token = base64.b64encode(
            f"{auth.basic.username}:{auth.basic.password}".encode("utf-8")
        ).decode("ascii")
        out_headers["Authorization"] = f"Basic {token}"
        return out_url, out_headers

    if auth.kind == "bearer" and auth.token:
        out_headers["Authorization"] = f"Bearer {auth.token}"
        return out_url, out_headers

    if auth.kind == "api-key" and auth.token:
        if spec.api_key_in == "query":
            parts = urlsplit(url)
            q = dict(parse_qsl(parts.query, keep_blank_values=True))
            q[spec.api_key_name] = auth.token
            out_url = urlunsplit(
                (parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment)
            )
        else:
            out_headers[spec.api_key_name] = auth.token
        return out_url, out_headers

    raise AuthError(f"DATEX {spec.id}: incomplete auth material")


def redact_secrets(text: str, auth: AuthMaterial | None = None) -> str:
    """Best-effort scrub of secret substrings from log/exception text."""
    if not text:
        return text
    out = text
    if auth is not None:
        if auth.token:
            out = out.replace(auth.token, "[REDACTED]")
        if auth.basic is not None:
            if auth.basic.password:
                out = out.replace(auth.basic.password, "[REDACTED]")
            if auth.basic.username:
                # username alone is less sensitive but still redact in URLs
                pass
    # Strip Basic blobs
    import re

    out = re.sub(r"(?i)Authorization:\s*Basic\s+\S+", "Authorization: Basic [REDACTED]", out)
    out = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._\-]+", "Bearer [REDACTED]", out)
    return out
