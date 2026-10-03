"""Minimal Osmosis .poly point-in-polygon (fallback if live lib unreadable)."""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence

PolyRings = list[list[tuple[float, float]]]


def parse_osmosis_poly(text: str) -> PolyRings:
    lines = [ln.strip() for ln in text.splitlines()]
    if not lines:
        raise ValueError("empty .poly")
    i = 0
    while i < len(lines) and not lines[i]:
        i += 1
    if i >= len(lines):
        raise ValueError("empty .poly")
    i += 1
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
            if cur is not None:
                raise ValueError("ring header while ring still open")
            cur = []
            continue
        if cur is None:
            raise ValueError(f"coordinate outside ring: {s}")
        lon = float(parts[0])
        lat = float(parts[1])
        cur.append((lon, lat))
    if cur is not None:
        raise ValueError("unclosed ring at EOF")
    if not rings:
        raise ValueError("no rings in .poly")
    return rings


def load_poly_file(path: Path | str) -> PolyRings:
    return parse_osmosis_poly(Path(path).read_text(encoding="utf-8", errors="replace"))


def _point_in_ring(lon: float, lat: float, ring: Sequence[tuple[float, float]]) -> bool:
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
