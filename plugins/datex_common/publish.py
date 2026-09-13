"""Atomic publish helpers for data/published/datex/<provider_id>/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def provider_publish_dir(pack_root: Path, provider_id: str) -> Path:
    """Canonical DocumentRoot-relative publish path for a provider."""
    pid = provider_id.strip().strip("/")
    if not pid or "/" in pid or pid in (".", ".."):
        raise ValueError(f"invalid DATEX provider_id: {provider_id!r}")
    return Path(pack_root) / "published" / "datex" / pid


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_bytes(data)
    partial.replace(path)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


def write_source_json(
    publish_dir: Path,
    meta: Mapping[str, Any],
    *,
    endpoints_ok: Sequence[str] | None = None,
) -> Path:
    """Write source.json under publish_dir (atomic). Caller supplies attribution fields.

    Always sets schema=1. Optionally overwrites ``endpoints`` from endpoints_ok.
    """
    payload = dict(meta)
    payload.setdefault("schema", 1)
    if endpoints_ok is not None:
        payload["endpoints"] = list(endpoints_ok)
    path = Path(publish_dir) / "source.json"
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path
