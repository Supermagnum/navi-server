"""Enforce that .rkyv publish/scrub only touches the packs tree.

The Apache DocumentRoot is ``data/published`` and hosts siblings:

- ``packs/`` — .rkyv generations (owned by the weekly bake / scrub)
- ``mwm/`` — CoMaps sidecars (owned by the MWM pipeline; marker ``.navi-mwm-owned``)
- ``datex/``, ``repeaters/``, …

.rkyv tools must use an **allow-list** under ``published/packs`` (and the
rkyv-only ``published/.staging``), and must refuse any path that resolves
into ``published/mwm`` or under a ``.navi-mwm-owned`` marker.
"""

from __future__ import annotations

from pathlib import Path

MWM_OWNED_MARKER = ".navi-mwm-owned"
MWM_DIRNAME = "mwm"
PACKS_DIRNAME = "packs"
RKYV_STAGING_NAME = ".staging"


class RkyvPublishedGuardError(RuntimeError):
    """Raised when a .rkyv tool would touch the MWM tree or an unknown path."""


def packs_root(published_dir: Path) -> Path:
    return Path(published_dir).resolve() / PACKS_DIRNAME


def mwm_root(published_dir: Path) -> Path:
    return Path(published_dir).resolve() / MWM_DIRNAME


def ensure_mwm_owned_marker(mwm_publish_root: Path) -> Path:
    """Write the ownership marker at the MWM publish root (idempotent)."""
    root = Path(mwm_publish_root)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MWM_OWNED_MARKER
    if not marker.is_file():
        marker.write_text(
            "owned-by=navi-mwm-pipeline\n"
            "do-not-touch-from=rkyv-scrub,cleanup,publish-packs\n",
            encoding="utf-8",
        )
    return marker


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def path_has_mwm_owned_marker(path: Path) -> bool:
    """True if path or any ancestor directory contains ``.navi-mwm-owned``."""
    cur = Path(path).resolve()
    if cur.is_file():
        cur = cur.parent
    # Walk up a bounded number of parents.
    for _ in range(64):
        if (cur / MWM_OWNED_MARKER).is_file():
            return True
        if cur.parent == cur:
            break
        cur = cur.parent
    return False


def assert_rkyv_mutation_allowed(
    path: Path,
    *,
    published_dir: Path,
    allow_rkyv_staging: bool = True,
) -> Path:
    """Resolve ``path`` and ensure it is under the .rkyv allow-list.

    Allowed:
      - ``{published}/packs/**``
      - ``{published}/.staging`` and children (rkyv leftover staging only)

    Forbidden:
      - anything under ``{published}/mwm``
      - any path with a ``.navi-mwm-owned`` ancestor
      - any other sibling under ``published/``
    """
    published = Path(published_dir).resolve()
    target = Path(path).resolve()
    packs = packs_root(published)
    mwm = mwm_root(published)
    staging = published / RKYV_STAGING_NAME

    if path_has_mwm_owned_marker(target):
        raise RkyvPublishedGuardError(
            f"refusing path under .navi-mwm-owned marker: {target}"
        )
    if _is_under(target, mwm) or target == mwm:
        raise RkyvPublishedGuardError(
            f"refusing path under published/mwm: {target}"
        )
    if _is_under(target, packs) or target == packs:
        return target
    if allow_rkyv_staging and (_is_under(target, staging) or target == staging):
        # published/.staging must not be confused with published/mwm/.staging
        if MWM_DIRNAME in target.parts:
            raise RkyvPublishedGuardError(
                f"refusing staging path that includes mwm/: {target}"
            )
        return target
    raise RkyvPublishedGuardError(
        f"refusing path outside .rkyv allow-list "
        f"(packs/ or published/.staging only): {target}"
    )


def plan_safe_or_raise(paths: list[Path], *, published_dir: Path) -> list[str]:
    """Validate a dry-run plan; return resolved path strings."""
    out: list[str] = []
    for p in paths:
        out.append(str(assert_rkyv_mutation_allowed(p, published_dir=published_dir)))
    return out
