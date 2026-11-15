"""Rebuild data/published/datex/providers.json (no secrets)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional  # Mapping used by rebuild_from_specs

from .enable import provider_enabled
from .publish import atomic_write_text, provider_publish_dir

# Known built-in provider ids. Future plugins append here or pass providers=.
PROVIDER_ID_NPRA = "npra"

KNOWN_PROVIDERS: List[Dict[str, Any]] = [
    {
        "id": PROVIDER_ID_NPRA,
        "name": "NPRA (Statens vegvesen)",
        "auth": "basic",
        "enabled_env": "NAVI_DATEX_NPRA_ENABLED",
        "canonical_base": "/datex/npra/",
        "compat_redirects_from": "/datex/",
        "config_id": "no-vegvesen",
    },
]


def rebuild_providers_index_from_specs(
    pack_root: Path,
    specs: List[Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Build providers.json from ProviderSpec list (toml-driven).

    Public ``id`` remains ``publish_id`` / ``public_id`` so Vegvesen clients
    still see ``npra``. ``config_id`` carries the toml id (e.g. no-vegvesen).
    """
    from .provider_spec import ProviderSpec, provider_is_enabled

    env = environ if environ is not None else os.environ
    catalog: List[Dict[str, Any]] = []
    for spec in specs:
        if not isinstance(spec, ProviderSpec):
            continue
        pub_id = spec.public_id
        entry: Dict[str, Any] = {
            "id": pub_id,
            "config_id": spec.id,
            "name": spec.display_name,
            "auth": spec.auth_type,
            "enabled_env": spec.legacy_enabled_env
            or f"NAVI_DATEX_{spec.id.upper().replace('-', '_')}_ENABLED",
            "canonical_base": spec.canonical_base,
            "country": spec.country,
            "licence": spec.licence,
            "attribution": spec.attribution,
            "datex_version": spec.datex_version,
        }
        if pub_id == PROVIDER_ID_NPRA:
            entry["compat_redirects_from"] = "/datex/"
            entry["enabled_env"] = "NAVI_DATEX_NPRA_ENABLED"
        # Merge enabled from spec helper (legacy gate)
        entry["_enabled_override"] = provider_is_enabled(spec, env)
        catalog.append(entry)

    # Fall back to KNOWN_PROVIDERS if no specs
    if not catalog:
        return rebuild_providers_index(pack_root, environ=env)

    root = Path(pack_root)
    entries: List[Dict[str, Any]] = []
    for spec_entry in catalog:
        pid = str(spec_entry["id"])
        pub = provider_publish_dir(root, pid)
        enabled_env = str(spec_entry.get("enabled_env") or "")
        enabled = bool(spec_entry.pop("_enabled_override", False))
        if enabled_env and enabled_env in env:
            enabled = provider_enabled(pid, env, env_key=enabled_env) or enabled
        entries.append(
            {
                "id": pid,
                "config_id": spec_entry.get("config_id"),
                "name": spec_entry.get("name", pid),
                "auth": spec_entry.get("auth", "unknown"),
                "enabled": enabled,
                "enabled_env": enabled_env,
                "canonical_base": spec_entry.get("canonical_base", f"/datex/{pid}/"),
                "compat_redirects_from": spec_entry.get("compat_redirects_from"),
                "has_data": _has_published_data(pub),
                "publish_relpath": f"datex/{pid}",
                "country": spec_entry.get("country"),
                "licence": spec_entry.get("licence"),
                "attribution": spec_entry.get("attribution"),
                "datex_version": spec_entry.get("datex_version"),
            }
        )
    payload = {
        "schema": 1,
        "note": (
            "DATEX provider registry for this host. Credentials are never listed. "
            "Clients should prefer canonical_base paths; flat /datex/<file> URLs "
            "may redirect to /datex/npra/ for NPRA compatibility."
        ),
        "providers": entries,
    }
    out = root / "published" / "datex" / "providers.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return out


def _has_published_data(publish_dir: Path) -> bool:
    if not publish_dir.is_dir():
        return False
    if (publish_dir / "source.json").is_file():
        return True
    return any(publish_dir.glob("*.xml"))


def rebuild_providers_index(
    pack_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
    providers: Optional[List[Dict[str, Any]]] = None,
) -> Path:
    """Write published/datex/providers.json describing installed providers.

    Does not create provider subdirs. Does not read secrets files.
    """
    env = environ if environ is not None else os.environ
    root = Path(pack_root)
    catalog = providers if providers is not None else KNOWN_PROVIDERS
    entries: List[Dict[str, Any]] = []
    for spec in catalog:
        pid = str(spec["id"])
        pub = provider_publish_dir(root, pid)
        enabled_env = str(spec.get("enabled_env") or f"NAVI_DATEX_{pid.upper()}_ENABLED")
        entries.append(
            {
                "id": pid,
                "name": spec.get("name", pid),
                "auth": spec.get("auth", "unknown"),
                "enabled": provider_enabled(pid, env, env_key=enabled_env),
                "enabled_env": enabled_env,
                "canonical_base": spec.get("canonical_base", f"/datex/{pid}/"),
                "compat_redirects_from": spec.get("compat_redirects_from"),
                "has_data": _has_published_data(pub),
                "publish_relpath": f"datex/{pid}",
            }
        )

    payload = {
        "schema": 1,
        "note": (
            "DATEX provider registry for this host. Credentials are never listed. "
            "Clients should prefer canonical_base paths; flat /datex/<file> URLs "
            "may redirect to /datex/npra/ for NPRA compatibility."
        ),
        "providers": entries,
    }
    out = root / "published" / "datex" / "providers.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return out
