"""Load DATEX provider specs from config/datex/providers/*.toml (no secrets)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional


class ProviderSpecError(ValueError):
    """Invalid provider configuration."""


AUTH_TYPES = frozenset({"none", "basic", "api-key", "bearer"})


@dataclass(frozen=True)
class Publication:
    name: str
    url: str
    interval_secs: int = 300
    enabled: bool = True
    use_conditional: bool = True


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    display_name: str
    country: str
    attribution: str
    licence: str
    datex_version: str
    auth_type: str
    publications: List[Publication]
    enabled: bool = True
    publish_id: str = ""
    source_label: str = ""
    user_agent: str = "navi-server-datex/1.0"
    timeout_secs: int = 60
    secrets_file: Optional[Path] = None
    legacy_enabled_env: str = ""
    api_key_in: str = "header"  # header | query
    api_key_name: str = "Authorization"
    accept_encoding_gzip: bool = False
    config_path: Optional[Path] = None

    @property
    def public_id(self) -> str:
        """Directory / providers.json id served to clients."""
        return self.publish_id or self.id

    @property
    def canonical_base(self) -> str:
        return f"/datex/{self.public_id}/"


def _as_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _clamp_interval(n: int) -> int:
    return n if n >= 60 else 60


def parse_provider_dict(data: Mapping[str, Any], *, config_path: Path | None = None) -> ProviderSpec:
    pid = str(data.get("id") or "").strip()
    if not pid or not pid.replace("-", "_").replace("_", "a").isalnum():
        # allow hyphen ids like no-vegvesen / fi-digitraffic
        import re

        if not re.match(r"^[a-z][a-z0-9_-]{0,63}$", pid or ""):
            raise ProviderSpecError(f"invalid provider id: {pid!r}")
    auth = str(data.get("auth_type") or "none").strip().lower()
    if auth not in AUTH_TYPES:
        raise ProviderSpecError(f"auth_type must be one of {sorted(AUTH_TYPES)}, got {auth!r}")

    pubs_raw = data.get("publications") or []
    if not isinstance(pubs_raw, list) or not pubs_raw:
        raise ProviderSpecError(f"{pid}: at least one [[publications]] entry required")
    pubs: List[Publication] = []
    for i, row in enumerate(pubs_raw):
        if not isinstance(row, Mapping):
            raise ProviderSpecError(f"{pid}: publications[{i}] must be a table")
        name = str(row.get("name") or "").strip()
        url = str(row.get("url") or "").strip()
        if not name or not url:
            raise ProviderSpecError(f"{pid}: publications[{i}] needs name and url")
        if "://" not in url:
            raise ProviderSpecError(f"{pid}: publication {name!r} url must be absolute")
        try:
            interval = _clamp_interval(int(row.get("interval_secs") or 300))
        except (TypeError, ValueError) as e:
            raise ProviderSpecError(f"{pid}: bad interval_secs for {name}") from e
        pubs.append(
            Publication(
                name=name,
                url=url,
                interval_secs=interval,
                enabled=_as_bool(row.get("enabled"), True),
                use_conditional=_as_bool(row.get("use_conditional"), True),
            )
        )

    secrets = data.get("secrets_file")
    secrets_path = Path(str(secrets)) if secrets else None
    try:
        timeout = int(data.get("timeout_secs") or 60)
    except (TypeError, ValueError) as e:
        raise ProviderSpecError(f"{pid}: bad timeout_secs") from e

    return ProviderSpec(
        id=pid,
        publish_id=str(data.get("publish_id") or "").strip(),
        display_name=str(data.get("display_name") or pid),
        country=str(data.get("country") or ""),
        attribution=str(data.get("attribution") or ""),
        licence=str(data.get("licence") or data.get("license") or ""),
        source_label=str(data.get("source_label") or data.get("display_name") or pid),
        datex_version=str(data.get("datex_version") or ""),
        auth_type=auth,
        publications=pubs,
        enabled=_as_bool(data.get("enabled"), True),
        user_agent=str(data.get("user_agent") or "navi-server-datex/1.0"),
        timeout_secs=max(5, timeout),
        secrets_file=secrets_path,
        legacy_enabled_env=str(data.get("legacy_enabled_env") or "").strip(),
        api_key_in=str(data.get("api_key_in") or "header").strip().lower(),
        api_key_name=str(data.get("api_key_name") or "Authorization"),
        accept_encoding_gzip=_as_bool(data.get("accept_encoding_gzip"), False),
        config_path=config_path,
    )


def load_provider_file(path: Path) -> ProviderSpec:
    text = path.read_bytes()
    data = tomllib.loads(text.decode("utf-8"))
    return parse_provider_dict(data, config_path=path)


def default_providers_dir(repo_root: Path) -> Path:
    return repo_root / "config" / "datex" / "providers"


def iter_provider_files(providers_dir: Path) -> Iterable[Path]:
    if not providers_dir.is_dir():
        return []
    return sorted(providers_dir.glob("*.toml"))


def load_all_providers(providers_dir: Path) -> List[ProviderSpec]:
    out: List[ProviderSpec] = []
    for path in iter_provider_files(providers_dir):
        out.append(load_provider_file(path))
    return out


def provider_is_enabled(spec: ProviderSpec, environ: Mapping[str, str] | None = None) -> bool:
    """Config enabled flag, optionally gated by legacy_enabled_env."""
    from .enable import is_truthy, provider_enabled

    env = environ if environ is not None else __import__("os").environ
    if not spec.enabled:
        return False
    if spec.legacy_enabled_env:
        # Fail-closed on legacy gate when present (NPRA).
        return is_truthy(env.get(spec.legacy_enabled_env), default=False)
    # Generic providers: enabled in toml is enough; optional NAVI_DATEX_<ID>_ENABLED
    env_key = f"NAVI_DATEX_{spec.id.upper().replace('-', '_')}_ENABLED"
    if env_key in env:
        return provider_enabled(spec.id, env, env_key=env_key)
    return True
