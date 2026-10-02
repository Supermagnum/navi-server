"""DEM tile coverage helpers for convert / validate guards.

Copernicus GLO-30 cache layout (see prefetch-dem-bbox.py):
  elev_dir/copernicus/<NxxExxx>/Copernicus_DSM_COG_10_….tif

Also accepts loose *.tif / *.hgt / *.geotiff anywhere under elev_dir (SRTM /
viewfinder). Coverage for a bbox is the fraction of 1-degree cells that have
at least one non-empty DEM payload on disk.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable

DEM_GLOB_SUFFIXES = (".tif", ".tiff", ".geotiff", ".hgt", ".HGT")


def tile_stem(lat_floor: int, lon_floor: int) -> str:
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    return f"{ns}{abs(lat_floor):02d}{ew}{abs(lon_floor):03d}"


def bbox_cells(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float
) -> list[tuple[int, int]]:
    cells: list[tuple[int, int]] = []
    for lat in range(math.floor(min_lat), math.floor(max_lat) + 1):
        for lon in range(math.floor(min_lon), math.floor(max_lon) + 1):
            cells.append((lat, lon))
    return cells


def _is_dem_file(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        if path.stat().st_size <= 0:
            return False
    except OSError:
        return False
    name = path.name
    lower = name.lower()
    return lower.endswith(DEM_GLOB_SUFFIXES)


def cell_has_dem(elev_dir: Path, lat_floor: int, lon_floor: int) -> bool:
    """True when the Copernicus stem dir has a DEM payload, or a loose DEM
    whose name contains the stem (best-effort for non-Copernicus layouts)."""
    elev = Path(elev_dir)
    stem = tile_stem(lat_floor, lon_floor)
    cop = elev / "copernicus" / stem
    if cop.is_dir():
        try:
            for p in cop.iterdir():
                if _is_dem_file(p):
                    return True
        except OSError:
            pass
    # Loose / alternate trees (srtm, viewfinder, flat caches).
    for sub in (elev / "srtm", elev / "viewfinder", elev):
        if not sub.is_dir():
            continue
        try:
            for p in sub.rglob("*"):
                if stem in p.name and _is_dem_file(p):
                    return True
        except OSError:
            continue
    return False


def elev_has_any_dem(elev_dir: Path) -> bool:
    elev = Path(elev_dir)
    if not elev.is_dir():
        return False
    try:
        for p in elev.rglob("*"):
            if _is_dem_file(p):
                return True
    except OSError:
        return False
    return False


def coverage_for_bbox(
    elev_dir: Path,
    min_lat: float,
    min_lon: float,
    max_lat: float,
    max_lon: float,
) -> tuple[int, int]:
    """Return (present_cells, total_cells) for the bbox grid."""
    cells = bbox_cells(min_lat, min_lon, max_lat, max_lon)
    if not cells:
        return 0, 0
    present = sum(
        1 for lat, lon in cells if cell_has_dem(Path(elev_dir), lat, lon)
    )
    return present, len(cells)


def load_bbox(
    bboxes_path: Path, region_id: str
) -> tuple[float, float, float, float] | None:
    if not bboxes_path.is_file():
        return None
    try:
        data = json.loads(bboxes_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    b = data.get(region_id)
    if not b or len(b) != 4:
        return None
    return float(b[0]), float(b[1]), float(b[2]), float(b[3])


def region_dem_ok(
    elev_dir: Path,
    region_id: str,
    bboxes_path: Path | None = None,
) -> tuple[bool, str]:
    """Decide whether convert may bake Δh for region_id.

    With a bbox entry: require at least one DEM cell present for that grid.
    Without a bbox: require any DEM payload under elev_dir (legacy fallback;
    validate catches empty-Δh regressions).
    """
    elev = Path(elev_dir)
    if not elev.is_dir():
        return False, f"elev dir missing: {elev}"
    bbox = None
    if bboxes_path is not None:
        bbox = load_bbox(Path(bboxes_path), region_id)
    if bbox is not None:
        present, total = coverage_for_bbox(elev, *bbox)
        if present <= 0:
            return (
                False,
                f"no DEM tiles covering {region_id} "
                f"(0/{total} cells under {elev}; bbox={bbox})",
            )
        return True, f"dem coverage {region_id} present={present}/{total}"
    if not elev_has_any_dem(elev):
        return False, f"no DEM tiles (*.tif/*.hgt) under {elev}"
    return (
        True,
        f"dem present under {elev} (no bbox for {region_id}; "
        "coverage not region-scoped)",
    )


def iter_dem_files(elev_dir: Path) -> Iterable[Path]:
    elev = Path(elev_dir)
    if not elev.is_dir():
        return
    for p in elev.rglob("*"):
        if _is_dem_file(p):
            yield p
