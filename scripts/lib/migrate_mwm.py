"""Allow-list planner + verifier for MWM-only host migration.

Copies only published MWM releases (and optional caches / config). Never
includes .rkyv packs, generations, root current.json, scratch, logs, or locks.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

MWM_OWNED_MARKER = ".navi-mwm-owned"

# Basename / path fragments that must never appear in a planned transfer.
FORBIDDEN_FRAGMENTS = (
    "/packs/",
    "/generations/",
    "/scratch/",
    "/staging/",
    "/logs/",
    "pack-pipeline.lock",
    ".rkyv",
    "navi-graph-",
    "navi-poi-",
    "navi-wetland.",
)

# Relative to source navi-server root.
DEFAULT_MWM_REL = Path("data/published/mwm")
DEFAULT_MAPTERHORN_REL = Path("data/elevation/mapterhorn-cache")
DEFAULT_CONFIG_MWM_REL = Path("config/mwm")


class MigrateMwmError(RuntimeError):
    """Raised when a path escapes the allow-list or verification fails."""


@dataclass
class PlannedFile:
    """One file to copy; ``rel`` is relative to the destination root layout."""

    src: Path
    rel: Path  # under dest root (e.g. data/published/mwm/current.json)
    size: int
    sha256: str | None = None  # filled on demand / from checksums


@dataclass
class MigratePlan:
    files: list[PlannedFile] = field(default_factory=list)
    total_bytes: int = 0
    current_json_rel: Path | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "file_count": len(self.files),
            "total_bytes": self.total_bytes,
            "current_json": str(self.current_json_rel) if self.current_json_rel else None,
            "files": [
                {"rel": str(f.rel), "size": f.size, "sha256": f.sha256} for f in self.files
            ],
            "errors": self.errors,
        }


def _sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def resolve_under(root: Path, candidate: Path) -> Path:
    """Resolve ``candidate`` and require it stays under ``root``."""
    root_r = root.resolve()
    cand = candidate.expanduser()
    if not cand.is_absolute():
        cand = root_r / cand
    resolved = cand.resolve()
    try:
        resolved.relative_to(root_r)
    except ValueError as e:
        raise MigrateMwmError(
            f"path escapes allow-list root: {candidate} -> {resolved} (root={root_r})"
        ) from e
    return resolved


def assert_not_forbidden(rel_posix: str) -> None:
    low = rel_posix.replace("\\", "/").lstrip("/")
    # Root published/current.json is .rkyv — only mwm/current.json is allowed.
    if low == "data/published/current.json":
        raise MigrateMwmError(f"forbidden .rkyv current.json: {rel_posix}")
    if low.endswith(".rkyv") or "/packs/" in f"/{low}/" or low.startswith("data/published/packs"):
        raise MigrateMwmError(f"forbidden .rkyv/packs path: {rel_posix}")
    for frag in (
        "/generations/",
        "/scratch/",
        "/staging/",
        "/logs/",
        "pack-pipeline.lock",
        "navi-graph-",
        "navi-poi-",
    ):
        if frag in f"/{low}/" or frag in low:
            raise MigrateMwmError(f"forbidden path fragment {frag!r} in {rel_posix}")


def load_release_checksums(release_dir: Path) -> dict[str, str]:
    """Map basename -> sha256 from checksums.sha256 (GNU coreutils format)."""
    path = release_dir / "checksums.sha256"
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line)
        if not m:
            continue
        out[m.group(2).strip()] = m.group(1).lower()
    return out


def _iter_files(directory: Path) -> Iterable[Path]:
    if not directory.is_dir():
        return
    for p in sorted(directory.rglob("*")):
        if p.is_file() and not p.is_symlink():
            yield p
        elif p.is_symlink() and p.exists() and p.is_file():
            # Follow only if still under parent (resolved later)
            yield p


def build_plan(
    source_root: Path,
    *,
    with_geometry_cache: bool = False,
    geometry_cache: Path | None = None,
    with_mapterhorn_cache: bool = False,
    mapterhorn_cache: Path | None = None,
    with_config: bool = True,
    with_secrets: bool = False,
    secrets_paths: list[Path] | None = None,
    hash_now: bool = False,
) -> MigratePlan:
    """Build an allow-listed file plan under ``source_root`` (navi-server root)."""
    root = source_root.resolve()
    plan = MigratePlan()

    mwm = resolve_under(root, DEFAULT_MWM_REL)
    if not mwm.is_dir():
        plan.errors.append(f"missing MWM published root: {mwm}")
        return plan
    marker = mwm / MWM_OWNED_MARKER
    if not marker.is_file():
        plan.errors.append(f"missing {MWM_OWNED_MARKER} under {mwm}")
        return plan

    allowed_roots: list[Path] = [mwm.resolve()]

    def add_file(src: Path, rel: Path, *, roots: list[Path] | None = None) -> None:
        rel_s = rel.as_posix()
        assert_not_forbidden(rel_s)
        src_r = src.resolve()
        check_roots = roots if roots is not None else allowed_roots
        if not any(_is_under(src_r, ar) for ar in check_roots):
            raise MigrateMwmError(f"source not under allowed roots: {src_r}")
        size = src.stat().st_size
        sha = _sha256_file(src) if hash_now else None
        plan.files.append(PlannedFile(src=src, rel=rel, size=size, sha256=sha))
        plan.total_bytes += size

    # MWM tree — defer current.json to end
    current = mwm / "current.json"
    for src in _iter_files(mwm):
        rel = Path("data/published/mwm") / src.relative_to(mwm)
        if src.resolve() == current.resolve():
            continue
        add_file(src, rel)

    if with_config:
        cfg = root / DEFAULT_CONFIG_MWM_REL
        if cfg.is_dir():
            cfg_r = cfg.resolve()
            allowed_roots.append(cfg_r)
            for src in _iter_files(cfg):
                rel = DEFAULT_CONFIG_MWM_REL / src.relative_to(cfg)
                add_file(src, rel, roots=[cfg_r])

    if with_mapterhorn_cache:
        mh = mapterhorn_cache or (root / DEFAULT_MAPTERHORN_REL)
        if not Path(mh).is_absolute():
            mh = resolve_under(root, Path(mh))
        else:
            mh = Path(mh).resolve()
            # Absolute caches must still look like a mapterhorn cache dir.
            if "mapterhorn" not in mh.as_posix():
                raise MigrateMwmError(f"mapterhorn cache path not recognized: {mh}")
        if mh.is_dir():
            allowed_roots.append(mh)
            for src in _iter_files(mh):
                rel = Path("data/elevation/mapterhorn-cache") / src.relative_to(mh)
                add_file(src, rel, roots=[mh])

    if with_geometry_cache:
        gc = geometry_cache
        if gc is None:
            env = os.environ.get("NAVI_MWM_GEOMETRY_CACHE", "").strip()
            gc = Path(env) if env else root / "data/elevation/geometry-hash-cache"
        if not Path(gc).is_absolute():
            gc = resolve_under(root, Path(gc))
        else:
            gc = Path(gc).resolve()
        if gc.is_dir():
            allowed_roots.append(gc)
            for src in _iter_files(gc):
                rel = Path("data/elevation/geometry-hash-cache") / src.relative_to(gc)
                add_file(src, rel, roots=[gc])

    if with_secrets:
        paths = secrets_paths or [Path("/etc/navi/mwm.env"), root / "data/secrets"]
        for sp in paths:
            p = Path(sp)
            if not p.is_absolute():
                p = resolve_under(root, p)
            else:
                p = p.resolve()
            if p.is_file():
                add_file(p, Path("secrets") / p.name, roots=[p.parent])
            elif p.is_dir():
                for src in _iter_files(p):
                    if "mwm" not in src.name.lower() and src.name != "mwm.env":
                        continue
                    add_file(src, Path("secrets") / src.relative_to(p), roots=[p])

    if current.is_file():
        rel = Path("data/published/mwm/current.json")
        add_file(current, rel)
        plan.current_json_rel = rel

    return plan


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def attach_checksums_from_release(plan: MigratePlan, source_root: Path) -> None:
    """Fill sha256 from release checksums.sha256 when available; else compute."""
    root = source_root.resolve()
    mwm = root / DEFAULT_MWM_REL
    # Group by release dir
    by_release: dict[Path, dict[str, str]] = {}
    for f in plan.files:
        parts = f.rel.parts
        # data/published/mwm/<ver>/file
        if len(parts) >= 4 and parts[0:3] == ("data", "published", "mwm"):
            ver = parts[3]
            if ver in (".navi-mwm-owned", "current.json"):
                continue
            rel_dir = mwm / ver
            if rel_dir not in by_release:
                by_release[rel_dir] = load_release_checksums(rel_dir)
    for f in plan.files:
        if f.sha256:
            continue
        parts = f.rel.parts
        if len(parts) >= 5 and parts[0:3] == ("data", "published", "mwm"):
            ver_dir = mwm / parts[3]
            table = by_release.get(ver_dir, {})
            digest = table.get(parts[-1]) or table.get("/".join(parts[4:]))
            if digest:
                f.sha256 = digest
                continue
        f.sha256 = _sha256_file(f.src)


def verify_copied_file(dest_file: Path, expected_sha: str) -> None:
    got = _sha256_file(dest_file)
    if got != expected_sha.lower():
        raise MigrateMwmError(f"checksum mismatch {dest_file}: got {got} want {expected_sha}")


def count_forbidden_in_plan(plan: MigratePlan) -> int:
    n = 0
    for f in plan.files:
        rel = f.rel.as_posix()
        try:
            assert_not_forbidden(rel)
        except MigrateMwmError:
            n += 1
    return n


def plan_summary_json(plan: MigratePlan) -> str:
    return json.dumps(
        {
            "file_count": len(plan.files),
            "total_bytes": plan.total_bytes,
            "forbidden_count": count_forbidden_in_plan(plan),
            "current_json": str(plan.current_json_rel) if plan.current_json_rel else None,
            "errors": plan.errors,
        },
        indent=2,
    )
