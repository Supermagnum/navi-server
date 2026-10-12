"""Atomic publish under /repeaters/<source>/<slug>/<generation>/."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from plugins.datex_common.publish import atomic_write_text

from . import SCHEMA_VERSION

LOG = logging.getLogger("navi.repeater_mirror")

DATA_FILENAME = "repeaters.json.zst"
MANIFEST_FILENAME = "manifest.json"
CHECKSUMS_FILENAME = "checksums.sha256"


class PublishError(RuntimeError):
    pass


def new_generation_id(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def compress_zstd(data: bytes) -> bytes:
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


def validate_source_id(source: str) -> str:
    s = (source or "").strip().lower()
    if not s or "/" in s or s in (".", "..") or not s.replace("_", "").isalnum():
        raise PublishError(f"invalid source id: {source!r}")
    return s


def _read_prev_count(publish_root: Path, source: str, country_slug: str) -> Optional[int]:
    current = publish_root / "current.json"
    if current.is_file():
        try:
            doc = json.loads(current.read_text(encoding="utf-8"))
            for src in doc.get("sources") or []:
                if src.get("source") != source:
                    continue
                for c in src.get("countries") or []:
                    if c.get("country_slug") == country_slug:
                        n = c.get("record_count")
                        if isinstance(n, int):
                            return n
        except (OSError, json.JSONDecodeError):
            pass
    country_dir = publish_root / source / country_slug
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
    source: str,
    country: str,
    country_slug: str,
    generation: str,
    fetched_unix: int,
    envelope: Mapping[str, Any],
    attribution: Mapping[str, str],
    licence: Optional[str] = None,
) -> Dict[str, Any]:
    stage_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = compress_zstd(payload)
    (stage_dir / DATA_FILENAME).write_bytes(compressed)
    record_count = int(envelope.get("record_count") or 0)
    digest = sha256_hex(compressed)
    manifest: Dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "source": source,
        "country": country,
        "country_slug": country_slug,
        "generation": generation,
        "fetched_unix": fetched_unix,
        "record_count": record_count,
        "attribution": dict(attribution),
        "files": {
            DATA_FILENAME: {"sha256": digest, "bytes": len(compressed)},
        },
    }
    if licence:
        manifest["licence"] = licence
    atomic_write_text(
        stage_dir / MANIFEST_FILENAME,
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    atomic_write_text(stage_dir / CHECKSUMS_FILENAME, f"{digest}  {DATA_FILENAME}\n")
    return manifest


def promote_generation(
    publish_root: Path,
    source: str,
    country_slug: str,
    generation: str,
    stage_dir: Path,
    *,
    keep_generations: int,
) -> Path:
    source = validate_source_id(source)
    dest_parent = publish_root / source / country_slug
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
        [p for p in country_dir.iterdir() if p.is_dir() and not p.name.startswith(".")],
        key=lambda p: p.name,
        reverse=True,
    )
    for old in gens[keep:]:
        shutil.rmtree(old, ignore_errors=True)
        LOG.info("pruned old generation path=%s", old)


def country_meta_from_manifest(
    manifest: Mapping[str, Any],
    *,
    source: str,
    country_slug: str,
    generation: str,
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "country": manifest.get("country"),
        "country_slug": country_slug,
        "generation": generation,
        "record_count": manifest.get("record_count"),
        "fetched_unix": manifest.get("fetched_unix"),
        "manifest_url": (
            f"/repeaters/{source}/{country_slug}/{generation}/{MANIFEST_FILENAME}"
        ),
        "data_url": f"/repeaters/{source}/{country_slug}/{generation}/{DATA_FILENAME}",
        "attribution": manifest.get("attribution"),
    }
    if manifest.get("licence"):
        meta["licence"] = manifest["licence"]
    # Optional join stats (RadioID)
    for k in ("matched", "api_only", "map_only", "join_key"):
        if k in manifest:
            meta[k] = manifest[k]
    return meta


def stage_and_validate(
    publish_root: Path,
    *,
    source: str,
    country: str,
    country_slug: str,
    generation: str,
    fetched_unix: int,
    envelope: Mapping[str, Any],
    stage_root: Path,
    attribution: Mapping[str, str],
    licence: Optional[str],
    ratio_min: float,
    ratio_max: float,
    extra_manifest: Optional[Mapping[str, Any]] = None,
) -> Tuple[Path, Dict[str, Any]]:
    source = validate_source_id(source)
    record_count = int(envelope.get("record_count") or 0)
    prev = _read_prev_count(publish_root, source, country_slug)
    validate_count(record_count, prev, ratio_min=ratio_min, ratio_max=ratio_max)
    stage_dir = stage_root / source / country_slug / generation
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    manifest = write_generation_tree(
        stage_dir,
        source=source,
        country=country,
        country_slug=country_slug,
        generation=generation,
        fetched_unix=fetched_unix,
        envelope=envelope,
        attribution=attribution,
        licence=licence,
    )
    if extra_manifest:
        manifest.update(dict(extra_manifest))
        atomic_write_text(
            stage_dir / MANIFEST_FILENAME,
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        )
    return stage_dir, manifest


def load_current(publish_root: Path) -> Dict[str, Any]:
    path = publish_root / "current.json"
    if not path.is_file():
        return {"schema": SCHEMA_VERSION, "sources": []}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": SCHEMA_VERSION, "sources": []}
    if "sources" not in doc:
        doc["sources"] = []
    return doc


def upsert_source(
    doc: Dict[str, Any],
    *,
    source: str,
    attribution: Mapping[str, str],
    licence: Optional[str],
    countries: Sequence[Mapping[str, Any]],
    generation: str,
    fetched_unix: int,
) -> Dict[str, Any]:
    """Replace one source entry; preserve other sources' countries."""
    source = validate_source_id(source)
    sources: List[Dict[str, Any]] = list(doc.get("sources") or [])
    entry: Dict[str, Any] = {
        "source": source,
        "generation": generation,
        "fetched_unix": fetched_unix,
        "attribution": dict(attribution),
        "countries": list(countries),
    }
    if licence:
        entry["licence"] = licence
    out: List[Dict[str, Any]] = []
    replaced = False
    for s in sources:
        if s.get("source") == source:
            # Merge country list: update countries we published; keep others.
            prev_by_slug = {
                c.get("country_slug"): c for c in (s.get("countries") or []) if c.get("country_slug")
            }
            for c in countries:
                prev_by_slug[c["country_slug"]] = c
            entry["countries"] = list(prev_by_slug.values())
            out.append(entry)
            replaced = True
        else:
            out.append(s)
    if not replaced:
        out.append(entry)
    doc = dict(doc)
    doc["schema"] = SCHEMA_VERSION
    doc["generation"] = generation
    doc["fetched_unix"] = fetched_unix
    doc["sources"] = out
    return doc


def write_current_json(publish_root: Path, doc: Mapping[str, Any]) -> Path:
    publish_root.mkdir(parents=True, exist_ok=True)
    path = publish_root / "current.json"
    atomic_write_text(path, json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return path
