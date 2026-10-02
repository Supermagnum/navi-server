"""Persistent Copernicus ocean/404 negative cache under NAVI_ELEV_DIR.

File: elev_dir/copernicus_ocean_404.txt

Format:
  # source=copernicus
  # updated_unix=<epoch>
  N59E005
  …

Stems previously HTTP 404'd are skipped on prefetch. Refresh with
prefetch-dem-bbox.py --refresh-404 (drop stems overlapping the request set)
or --refresh-404-all (clear the index).
"""

from __future__ import annotations

import fcntl
import time
from pathlib import Path
from typing import Iterable, Set

CACHE_NAME = "copernicus_ocean_404.txt"
SOURCE = "copernicus"


def cache_path(elev_dir: Path | str) -> Path:
    return Path(elev_dir) / CACHE_NAME


def load_missing(elev_dir: Path | str) -> Set[str]:
    p = cache_path(elev_dir)
    if not p.is_file():
        return set()
    out: Set[str] = set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.add(s)
    return out


def _write_index(path: Path, stems: Set[str]) -> None:
    lines = [
        f"# source={SOURCE}",
        f"# updated_unix={int(time.time())}",
        f"# count={len(stems)}",
    ]
    lines.extend(sorted(stems))
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)


def remember_missing(elev_dir: Path | str, stems: Iterable[str]) -> int:
    """Append new ocean-404 stems under flock. Returns count newly added."""
    elev = Path(elev_dir)
    elev.mkdir(parents=True, exist_ok=True)
    p = cache_path(elev)
    new = [s for s in stems if s]
    if not new:
        return 0
    added = 0
    with p.open("a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.seek(0)
        existing = {
            ln.strip()
            for ln in f.read().splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        }
        before = len(existing)
        for stem in new:
            existing.add(stem)
        added = len(existing) - before
        if added:
            f.seek(0)
            f.truncate()
            f.write(f"# source={SOURCE}\n")
            f.write(f"# updated_unix={int(time.time())}\n")
            f.write(f"# count={len(existing)}\n")
            for stem in sorted(existing):
                f.write(stem + "\n")
            f.flush()
    return added


def refresh_missing(
    elev_dir: Path | str, stems: Iterable[str] | None = None
) -> int:
    """Remove stems from the 404 index so they can be re-requested.

    stems=None clears the entire index. Returns number of stems removed.
    """
    elev = Path(elev_dir)
    p = cache_path(elev)
    if not p.is_file():
        return 0
    existing = load_missing(elev)
    if stems is None:
        removed = len(existing)
        if removed:
            _write_index(p, set())
        return removed
    drop = {s for s in stems if s}
    kept = existing - drop
    removed = len(existing) - len(kept)
    if removed:
        _write_index(p, kept)
    return removed
