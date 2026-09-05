"""Persistent Copernicus ocean-404 negative cache under NAVI_ELEV_DIR.

Stores one HGT/Copernicus tile stem per line (e.g. S19W032). Survives scratch
cleanup because it lives next to the DEM tree, not under scratch/. Bounded by
the global 1-degree grid (~65k cells); typical size is far smaller.
"""

from __future__ import annotations

import fcntl
from pathlib import Path
from typing import Iterable, Set

CACHE_NAME = "copernicus_ocean_404.txt"


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
        for stem in new:
            if stem in existing:
                continue
            f.write(stem + "\n")
            existing.add(stem)
            added += 1
        f.flush()
    return added
