"""Geofabrik / Osmosis .poly helpers for DEM ocean-skip prefetch.

Osmosis polygon filter format (same as Geofabrik publishes):
  <name>
  <ring-id>          # positive = outer, !N = hole
     <lon>   <lat>
     ...
  END
  ...
  END

DEM prefetch skips 1-degree Copernicus cells that do not intersect the
extract polygon at all. Partial intersection => fetch (conservative).
Missing/unparseable .poly => caller fails open (fetch all bbox cells).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Optional, Sequence

# Ring: list of (lon, lat). First ring is outer; subsequent are holes.
PolyRings = list[list[tuple[float, float]]]
Tile = tuple[int, int]


def parse_osmosis_poly(text: str) -> PolyRings:
    """Parse Osmosis/Geofabrik .poly text into rings (lon, lat)."""
    lines = [ln.strip() for ln in text.splitlines()]
    if not lines:
        raise ValueError("empty .poly")
    # First non-empty line is the polygon name.
    i = 0
    while i < len(lines) and not lines[i]:
        i += 1
    if i >= len(lines):
        raise ValueError("empty .poly")
    i += 1  # skip name

    rings: PolyRings = []
    cur: Optional[list[tuple[float, float]]] = None
    file_ended = False
    while i < len(lines):
        s = lines[i]
        i += 1
        if not s:
            continue
        if s.upper() == "END":
            if cur is not None:
                if len(cur) < 3:
                    raise ValueError("ring has fewer than 3 vertices")
                rings.append(cur)
                cur = None
            else:
                file_ended = True
                break
            continue
        parts = s.split()
        if len(parts) == 1:
            # Ring header (e.g. "1" or "!2").
            if cur is not None:
                raise ValueError("ring header while ring still open")
            cur = []
            continue
        if cur is None:
            raise ValueError(f"coordinate outside ring: {s}")
        if len(parts) < 2:
            raise ValueError(f"bad coordinate line: {s}")
        lon = float(parts[0])
        lat = float(parts[1])
        cur.append((lon, lat))
    if cur is not None:
        raise ValueError("unclosed ring at EOF")
    if not file_ended and not rings:
        raise ValueError("no rings in .poly")
    if not rings:
        raise ValueError("no rings in .poly")
    return rings


def load_poly_file(path: Path | str) -> PolyRings:
    return parse_osmosis_poly(Path(path).read_text(encoding="utf-8", errors="replace"))


def _point_in_ring(lon: float, lat: float, ring: Sequence[tuple[float, float]]) -> bool:
    """Ray casting; ring vertices are (lon, lat)."""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) if yj != yi else 1e-15) + xi
        ):
            inside = not inside
        j = i
    return inside


def point_in_poly(lon: float, lat: float, rings: PolyRings) -> bool:
    if not rings:
        return False
    if not _point_in_ring(lon, lat, rings[0]):
        return False
    for hole in rings[1:]:
        if _point_in_ring(lon, lat, hole):
            return False
    return True


def _segments_intersect(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    """Proper or touching segment intersection in 2D (lon/lat as x/y)."""

    def cross(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    def on_seg(p, q, r):
        return (
            min(p[0], q[0]) - 1e-12 <= r[0] <= max(p[0], q[0]) + 1e-12
            and min(p[1], q[1]) - 1e-12 <= r[1] <= max(p[1], q[1]) + 1e-12
        )

    d1 = cross(a, b, c)
    d2 = cross(a, b, d)
    d3 = cross(c, d, a)
    d4 = cross(c, d, b)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    ):
        return True
    if abs(d1) <= 1e-12 and on_seg(a, b, c):
        return True
    if abs(d2) <= 1e-12 and on_seg(a, b, d):
        return True
    if abs(d3) <= 1e-12 and on_seg(c, d, a):
        return True
    if abs(d4) <= 1e-12 and on_seg(c, d, b):
        return True
    return False


def tile_intersects_poly(
    lat0: int,
    lon0: int,
    rings: PolyRings,
    *,
    samples: int = 7,
) -> bool:
    """True if 1-degree cell [lat0,lat0+1] x [lon0,lon0+1] may overlap land.

    Conservative: any interior sample in poly, any tile corner in poly, any
    poly vertex in tile, or any poly edge crossing a tile edge => intersect.
    """
    if samples < 2:
        samples = 2
    # Dense interior + edge samples.
    for i in range(samples):
        for j in range(samples):
            lat = lat0 + (i + 0.5) / samples
            lon = lon0 + (j + 0.5) / samples
            if point_in_poly(lon, lat, rings):
                return True
    for lat in (float(lat0), float(lat0 + 1)):
        for lon in (float(lon0), float(lon0 + 1)):
            if point_in_poly(lon, lat, rings):
                return True

    tile_corners = [
        (float(lon0), float(lat0)),
        (float(lon0 + 1), float(lat0)),
        (float(lon0 + 1), float(lat0 + 1)),
        (float(lon0), float(lat0 + 1)),
    ]
    tile_edges = [
        (tile_corners[0], tile_corners[1]),
        (tile_corners[1], tile_corners[2]),
        (tile_corners[2], tile_corners[3]),
        (tile_corners[3], tile_corners[0]),
    ]

    for ring in rings:
        for lon, lat in ring:
            if lat0 <= lat <= lat0 + 1 and lon0 <= lon <= lon0 + 1:
                return True
        for k in range(len(ring)):
            a = ring[k]
            b = ring[(k + 1) % len(ring)]
            for e0, e1 in tile_edges:
                if _segments_intersect(a, b, e0, e1):
                    return True
    return False


def classify_bbox_tiles(
    bbox_tiles: Iterable[Tile],
    rings: PolyRings,
    *,
    samples: int = 7,
) -> tuple[list[Tile], list[Tile]]:
    """Split bbox tiles into (fetch, ocean_skip)."""
    fetch: list[Tile] = []
    skip: list[Tile] = []
    for tile in bbox_tiles:
        if tile_intersects_poly(tile[0], tile[1], rings, samples=samples):
            fetch.append(tile)
        else:
            skip.append(tile)
    return fetch, skip


def try_load_poly(path: Path | str | None) -> Optional[PolyRings]:
    """Return rings or None on missing/unreadable/invalid (fail-open signal)."""
    if not path:
        return None
    p = Path(path)
    if not p.is_file() or p.stat().st_size <= 0:
        return None
    try:
        rings = load_poly_file(p)
    except Exception:
        return None
    if not rings:
        return None
    return rings


def bbox_tiles(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> list[Tile]:
    return [
        (lat, lon)
        for lat in range(math.floor(min_lat), math.floor(max_lat) + 1)
        for lon in range(math.floor(min_lon), math.floor(max_lon) + 1)
    ]
