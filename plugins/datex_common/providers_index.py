"""Rebuild data/published/datex/providers.json (no secrets)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

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
    },
]


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
