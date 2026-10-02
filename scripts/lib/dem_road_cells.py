"""Build 1° DEM cell lists from every node on highway/ferry ways in a PBF.

Includes intermediate shape points (osmium locations=True on ways), not only
graph junction nodes. Cache beside pack state; invalidate when the PBF
fingerprint (size + mtime_ns) changes.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Iterable

CACHE_SCHEMA = 1


def tile_stem(lat_floor: int, lon_floor: int) -> str:
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    return f"{ns}{abs(lat_floor):02d}{ew}{abs(lon_floor):03d}"


def pbf_fingerprint(pbf: Path) -> dict:
    st = pbf.stat()
    return {
        "path": str(pbf.resolve()),
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
    }


def cells_cache_path(state_dir: Path, region_id: str) -> Path:
    return Path(state_dir) / "dem_cells" / f"{region_id}.json"


def load_cached_cells(
    state_dir: Path, region_id: str, pbf: Path
) -> list[tuple[int, int]] | None:
    path = cells_cache_path(state_dir, region_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("schema") != CACHE_SCHEMA:
        return None
    fp = pbf_fingerprint(pbf)
    cached_fp = data.get("pbf") or {}
    if cached_fp.get("size") != fp["size"] or cached_fp.get("mtime_ns") != fp["mtime_ns"]:
        return None
    cells = data.get("cells") or []
    out: list[tuple[int, int]] = []
    for c in cells:
        if isinstance(c, (list, tuple)) and len(c) == 2:
            out.append((int(c[0]), int(c[1])))
    return out


def save_cells_cache(
    state_dir: Path,
    region_id: str,
    pbf: Path,
    cells: Iterable[tuple[int, int]],
    *,
    ways: int = 0,
    nodes: int = 0,
) -> Path:
    path = cells_cache_path(state_dir, region_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    cell_list = sorted({(int(a), int(b)) for a, b in cells})
    payload = {
        "schema": CACHE_SCHEMA,
        "region_id": region_id,
        "pbf": pbf_fingerprint(pbf),
        "way_count": ways,
        "node_samples": nodes,
        "cell_count": len(cell_list),
        "cells": [[a, b] for a, b in cell_list],
        "stems": [tile_stem(a, b) for a, b in cell_list],
    }
    tmp = path.with_suffix(".json.partial")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def scan_pbf_road_cells(pbf: Path) -> tuple[list[tuple[int, int]], int, int]:
    """Return (cells, way_count, node_sample_count) for highway|ferry ways."""
    try:
        import osmium  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "pyosmium is required to build DEM road-cell lists from PBF"
        ) from e

    class Handler(osmium.SimpleHandler):
        def __init__(self) -> None:
            super().__init__()
            self.cells: set[tuple[int, int]] = set()
            self.ways = 0
            self.nodes = 0

        def way(self, w) -> None:  # noqa: ANN001
            tags = {t.k: t.v for t in w.tags}
            is_hwy = "highway" in tags
            is_ferry = tags.get("route") == "ferry" or "ferry" in tags
            if not (is_hwy or is_ferry):
                return
            self.ways += 1
            for n in w.nodes:
                if not n.location.valid():
                    continue
                lat = n.location.lat
                lon = n.location.lon
                self.cells.add((math.floor(lat), math.floor(lon)))
                self.nodes += 1

    h = Handler()
    h.apply_file(str(pbf), locations=True, idx="flex_mem")
    return sorted(h.cells), h.ways, h.nodes


def ensure_road_cells(
    state_dir: Path, region_id: str, pbf: Path, *, force: bool = False
) -> tuple[list[tuple[int, int]], Path, bool]:
    """Load or rebuild cached road cells. Returns (cells, cache_path, rebuilt)."""
    if not force:
        cached = load_cached_cells(state_dir, region_id, pbf)
        if cached is not None:
            return cached, cells_cache_path(state_dir, region_id), False
    cells, ways, nodes = scan_pbf_road_cells(pbf)
    path = save_cells_cache(
        state_dir, region_id, pbf, cells, ways=ways, nodes=nodes
    )
    return cells, path, True


def load_cells_file(path: Path) -> list[tuple[int, int]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [(int(a), int(b)) for a, b in data]
    cells = data.get("cells") or []
    out: list[tuple[int, int]] = []
    for c in cells:
        if isinstance(c, (list, tuple)) and len(c) == 2:
            out.append((int(c[0]), int(c[1])))
    return out


def cells_content_hash(cells: Iterable[tuple[int, int]]) -> str:
    blob = "\n".join(f"{a},{b}" for a, b in sorted(set(cells))).encode()
    return hashlib.sha256(blob).hexdigest()[:16]
