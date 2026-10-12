"""Atomic publish of /repeaters/ generations. Never overwrite good data with empty."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from plugins.datex_common.publish import atomic_write_bytes, atomic_write_text

from . import SCHEMA_VERSION
from .normalize import attribution_block

LOG = logging.getLogger("navi.repeaterbook")

DATA_FILENAME = "repeaters.json.zst"
MANIFEST_FILENAME = "manifest.json"
CHECKSUMS_FILENAME = "checksums.sha256"


class PublishError(RuntimeError):
    pass


def new_generation_id(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def compress_zstd(data: bytes) -> bytes:
    """Compress with the ``zstd`` CLI (no Python zstandard dependency)."""
    try:
        proc = subprocess.run(
            ["zstd", "-q", "-T1", "-19"],
            input=data,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PublishError(
            "zstd binary not found; install zstd to publish repeaters.json.zst"
        ) from exc
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:200]
        raise PublishError(f"zstd failed: {err}")
    return proc.stdout


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_prev_count(country_dir: Path) -> Optional[int]:
    current = country_dir.parent / "current.json"
    # Prefer country entry in top-level current.json when present.
    if current.is_file():
        try:
            doc = json.loads(current.read_text(encoding="utf-8"))
            for c in doc.get("countries") or []:
                if c.get("path", "").startswith(f"/repeaters/{country_dir.name}/"):
                    n = c.get("record_count")
                    if isinstance(n, int):
                        return n
        except (OSError, json.JSONDecodeError):
            pass
    # Fallback: newest generation manifest under country dir.
    if not country_dir.is_dir():
        return None
    gens = sorted(
        [p for p in country_dir.iterdir() if p.is_dir() and not p.name.startswith(".")],
        reverse=True,
    )
    for g in gens:
        man = g / MANIFEST_FILENAME
        if not man.is_file():
            continue
        try:
            m = json.loads(man.read_text(encoding="utf-8"))
            n = m.get("record_count")
            if isinstance(n, int):
                return n
        except (OSError, json.JSONDecodeError):
            continue
    return None


def validate_count(
    new_count: int,
    prev_count: Optional[int],
    *,
    ratio_min: float,
    ratio_max: float,
) -> None:
    if new_count <= 0:
        raise PublishError("refusing to publish empty repeater set")
    if prev_count is None or prev_count <= 0:
        return
    lo = prev_count * ratio_min
    hi = prev_count * ratio_max
    if new_count < lo or new_count > hi:
        raise PublishError(
            f"record_count {new_count} outside sane range vs previous {prev_count} "
            f"(allowed {lo:.0f}..{hi:.0f})"
        )


def write_generation_tree(
    stage_dir: Path,
    *,
    country: str,
    country_slug: str,
    generation: str,
    fetched_unix: int,
    envelope: Mapping[str, Any],
) -> Dict[str, Any]:
    """Write manifest + zstd payload + checksums into stage_dir."""
    stage_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = compress_zstd(payload)
    data_path = stage_dir / DATA_FILENAME
    data_path.write_bytes(compressed)

    record_count = int(envelope.get("record_count") or 0)
    digest = sha256_hex(compressed)
    manifest = {
        "schema": SCHEMA_VERSION,
        "country": country,
        "country_slug": country_slug,
        "generation": generation,
        "fetched_unix": fetched_unix,
        "record_count": record_count,
        "attribution": attribution_block(),
        "files": {
            DATA_FILENAME: {"sha256": digest, "bytes": len(compressed)},
        },
    }
    atomic_write_text(
        stage_dir / MANIFEST_FILENAME,
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    checksum_line = f"{digest}  {DATA_FILENAME}\n"
    atomic_write_text(stage_dir / CHECKSUMS_FILENAME, checksum_line)
    return manifest


def promote_generation(
    publish_root: Path,
    country_slug: str,
    generation: str,
    stage_dir: Path,
    *,
    keep_generations: int,
) -> Path:
    """Move staged generation into publish_root/<slug>/<gen>/ (atomic replace)."""
    dest_parent = publish_root / country_slug
    dest_parent.mkdir(parents=True, exist_ok=True)
    final = dest_parent / generation
    tmp = dest_parent / f".{generation}.partial"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(stage_dir, tmp)
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)
    _prune_old(dest_parent, keep=keep_generations)
    return final


def _prune_old(country_dir: Path, *, keep: int) -> None:
    gens = sorted(
        [
            p
            for p in country_dir.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        ],
        key=lambda p: p.name,
        reverse=True,
    )
    for old in gens[keep:]:
        shutil.rmtree(old, ignore_errors=True)
        LOG.info("pruned old generation path=%s", old)


def build_current_json(
    publish_root: Path,
    *,
    countries_meta: Sequence[Mapping[str, Any]],
    generation: str,
    fetched_unix: int,
) -> Dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "generation": generation,
        "fetched_unix": fetched_unix,
        "attribution": attribution_block(),
        "countries": list(countries_meta),
    }


def write_current_json(publish_root: Path, doc: Mapping[str, Any]) -> Path:
    publish_root.mkdir(parents=True, exist_ok=True)
    path = publish_root / "current.json"
    atomic_write_text(path, json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return path


def country_meta_from_manifest(
    manifest: Mapping[str, Any], *, country_slug: str, generation: str
) -> Dict[str, Any]:
    return {
        "country": manifest.get("country"),
        "country_slug": country_slug,
        "generation": generation,
        "record_count": manifest.get("record_count"),
        "fetched_unix": manifest.get("fetched_unix"),
        "manifest_url": f"/repeaters/{country_slug}/{generation}/{MANIFEST_FILENAME}",
        "data_url": f"/repeaters/{country_slug}/{generation}/{DATA_FILENAME}",
        "attribution": attribution_block(),
    }


def stage_and_validate(
    cfg_publish_root: Path,
    *,
    country: str,
    country_slug: str,
    generation: str,
    fetched_unix: int,
    envelope: Mapping[str, Any],
    stage_root: Path,
    ratio_min: float,
    ratio_max: float,
) -> Tuple[Path, Dict[str, Any]]:
    """Build a staged generation; raise PublishError if empty/outlier."""
    record_count = int(envelope.get("record_count") or 0)
    prev = _read_prev_count(cfg_publish_root / country_slug)
    validate_count(record_count, prev, ratio_min=ratio_min, ratio_max=ratio_max)
    stage_dir = stage_root / country_slug / generation
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    manifest = write_generation_tree(
        stage_dir,
        country=country,
        country_slug=country_slug,
        generation=generation,
        fetched_unix=fetched_unix,
        envelope=envelope,
    )
    return stage_dir, manifest
