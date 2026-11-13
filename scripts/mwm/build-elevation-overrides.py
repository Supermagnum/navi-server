#!/usr/bin/env python3
"""Sample Mapterhorn directly at dumped MWM road points; emit elevation JSONL.

Input: dump_mwm_roads JSONL with feature_id + points[{lat,lon,alt_m}].
Output: elevation override JSONL for navi-mwm-sidecar bake
  {"feature_id":N,"point_count":N,"points":[{"point_index":i,"altitude_dm":d},...]}

Only points whose |Mapterhorn - CoMaps| exceeds --threshold-m (default 2.0) are kept.
Tiles cached under --cache-dir with a soft size limit.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.request
from collections import OrderedDict
from pathlib import Path

DEFAULT_UA = (
    "navi-server-mwm/1.0 (+https://github.com/Supermagnum/navi-server; "
    "contact=mwm-pipeline)"
)
TILE_TMPL = "https://tiles.mapterhorn.com/{z}/{x}/{y}.webp"


def latlon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    lat_rad = math.radians(lat)
    n = 2.0**zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def terrarium_elev(r: int, g: int, b: int) -> float:
    return (r * 256.0 + g + b / 256.0) - 32768.0


class TileCache:
    """Mapterhorn Terrarium tile cache. HTTP 404 is a soft miss (try lower zoom)."""

    _MISSING = object()  # sentinel stored in _mem for known-missing tiles

    def __init__(self, cache_dir: Path, max_bytes: int, zoom: int, min_zoom: int = 10):
        self.cache_dir = cache_dir
        self.max_bytes = max_bytes
        self.zoom = zoom
        self.min_zoom = min_zoom
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem: OrderedDict[tuple[int, int, int], object] = OrderedDict()
        self._approx_bytes = 0
        self._fetches_since_enforce = 0
        self.hits = 0
        self.misses = 0
        self.fetches = 0
        self.not_found = 0
        self.fallback_ok = 0

    def _path(self, z: int, x: int, y: int) -> Path:
        return self.cache_dir / str(z) / str(x) / f"{y}.webp"

    def _miss_marker(self, z: int, x: int, y: int) -> Path:
        return self.cache_dir / str(z) / str(x) / f"{y}.404"

    def _remember_missing(self, key: tuple[int, int, int]) -> None:
        self._mem[key] = self._MISSING
        while len(self._mem) > 512:
            self._mem.popitem(last=False)

    def _remember_image(self, key: tuple[int, int, int], img) -> None:
        self._mem[key] = img
        while len(self._mem) > 512:
            self._mem.popitem(last=False)

    def _enforce_limit(self, force: bool = False) -> None:
        # Full tree walk is expensive; only run every N fetches or when over budget.
        self._fetches_since_enforce += 1
        if not force and self._fetches_since_enforce < 64 and self._approx_bytes < self.max_bytes:
            return
        self._fetches_since_enforce = 0
        total = 0
        files: list[tuple[float, Path]] = []
        for p in self.cache_dir.rglob("*.webp"):
            try:
                st = p.stat()
            except OSError:
                continue
            total += st.st_size
            files.append((st.st_mtime, p))
        self._approx_bytes = total
        if total <= self.max_bytes:
            return
        files.sort()
        for _, p in files:
            if total <= self.max_bytes:
                break
            try:
                sz = p.stat().st_size
                p.unlink()
                total -= sz
            except OSError:
                continue
        self._approx_bytes = total

    def _fetch_one(self, z: int, x: int, y: int):
        """Return PIL Image, or None if tile is missing (404) / unreadable."""
        from PIL import Image
        import io
        import urllib.error

        key = (z, x, y)
        if key in self._mem:
            self._mem.move_to_end(key)
            val = self._mem[key]
            if val is self._MISSING:
                self.hits += 1
                return None
            self.hits += 1
            return val

        path = self._path(z, x, y)
        miss = self._miss_marker(z, x, y)
        if miss.is_file():
            self.hits += 1
            self._remember_missing(key)
            return None

        data: bytes | None = None
        if path.is_file():
            data = path.read_bytes()
            self.hits += 1
        else:
            self.misses += 1
            url = TILE_TMPL.format(z=z, x=x, y=y)
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": os.environ.get("NAVI_MWM_HTTP_USER_AGENT", DEFAULT_UA)
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = resp.read()
                self.fetches += 1
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                self._approx_bytes += len(data)
                self._enforce_limit()
            except urllib.error.HTTPError as e:
                # Soft-miss: memoize so we never re-hit the network for this tile.
                self.not_found += 1
                if e.code == 404:
                    miss.parent.mkdir(parents=True, exist_ok=True)
                    miss.write_text("404\n")
                self._remember_missing(key)
                return None
            except Exception:
                # Timeouts / transient errors: memoize for this process only.
                self.not_found += 1
                self._remember_missing(key)
                return None

        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:
            self.not_found += 1
            self._remember_missing(key)
            return None
        self._remember_image(key, img)
        return img

    def get_image(self, lat: float, lon: float):
        """Fetch tile covering lat/lon at preferred zoom, falling back on 404."""
        for z in range(self.zoom, self.min_zoom - 1, -1):
            x, y = latlon_to_tile(lat, lon, z)
            img = self._fetch_one(z, x, y)
            if img is not None:
                if z != self.zoom:
                    self.fallback_ok += 1
                return img, z, x, y
        return None, self.zoom, 0, 0

    def sample(self, lat: float, lon: float) -> float | None:
        img, z, x, y = self.get_image(lat, lon)
        if img is None:
            return None
        n = 2.0**z
        w, h = img.size
        xf = ((lon + 180.0) / 360.0 * n - x) * w
        lat_rad = math.radians(lat)
        yf = ((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n - y) * h
        px = min(max(int(xf), 0), w - 1)
        py = min(max(int(yf), 0), h - 1)
        r, g, b = img.getpixel((px, py))
        return terrarium_elev(r, g, b)


def highest_working_zoom(lat: float, lon: float, preferred: int) -> int:
    for z in range(preferred, 9, -1):
        x, y = latlon_to_tile(lat, lon, z)
        url = TILE_TMPL.format(z=z, x=x, y=y)
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": DEFAULT_UA})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if int(resp.status) == 200:
                    return z
        except Exception:
            continue
    return 12


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roads-jsonl", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cache-dir", type=Path, required=True)
    ap.add_argument("--cache-max-bytes", type=int, default=2 * 1024**3)
    ap.add_argument("--threshold-m", type=float, default=2.0)
    ap.add_argument(
        "--zoom",
        type=int,
        default=0,
        help="Mapterhorn zoom; 0 = NAVI_MWM_MAPTERHORN_ZOOM or 12 (not max-zoom)",
    )
    ap.add_argument("--stats-out", type=Path, default=None)
    ap.add_argument("--max-features", type=int, default=0)
    args = ap.parse_args()

    # Default production zoom is 12. Auto-max (highest_working_zoom→15) is a
    # mountain pilot only — corridor same-point median stays ~0.5 m at z12.
    if args.zoom <= 0:
        args.zoom = int(os.environ.get("NAVI_MWM_MAPTERHORN_ZOOM", "12") or "12")
    zoom = args.zoom if args.zoom > 0 else 12
    with args.roads_jsonl.open() as f:
        first = f.readline()
    if not first:
        args.out.write_text("")
        return 0
    cache = TileCache(args.cache_dir, args.cache_max_bytes, zoom)

    total_points = 0
    override_points = 0
    features_out = 0
    t0 = time.monotonic()

    with args.roads_jsonl.open() as inp, args.out.open("w") as out:
        for i, line in enumerate(inp):
            if args.max_features and i >= args.max_features:
                break
            if not line.strip():
                continue
            feat = json.loads(line)
            fid = int(feat["feature_id"])
            points = feat["points"]
            point_count = len(points)
            overrides = []
            for idx, p in enumerate(points):
                total_points += 1
                mh = cache.sample(float(p["lat"]), float(p["lon"]))
                if mh is None:
                    continue
                comaps = float(p.get("alt_m", 0))
                if abs(mh - comaps) <= args.threshold_m:
                    continue
                dm = int(round(mh * 10.0))
                if dm < -32768 or dm > 32767:
                    continue
                overrides.append({"point_index": idx, "altitude_dm": dm})
                override_points += 1
            if not overrides:
                continue
            out.write(
                json.dumps(
                    {
                        "feature_id": fid,
                        "point_count": point_count,
                        "points": overrides,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )
            features_out += 1

    lookups = cache.hits + cache.misses
    stats = {
        "zoom": zoom,
        "threshold_m": args.threshold_m,
        "total_points": total_points,
        "override_points": override_points,
        "override_rate": (override_points / total_points) if total_points else 0.0,
        "features_with_overrides": features_out,
        "tile_cache_hits": cache.hits,
        "tile_cache_misses": cache.misses,
        "tile_cache_hit_rate": (cache.hits / lookups) if lookups else None,
        "tile_fetches": cache.fetches,
        "tile_not_found": cache.not_found,
        "tile_fallback_ok": cache.fallback_ok,
        "cache_dir": str(args.cache_dir),
        "cache_max_bytes": args.cache_max_bytes,
        "wall_s": time.monotonic() - t0,
    }
    if args.stats_out:
        args.stats_out.write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
