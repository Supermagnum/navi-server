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
    def __init__(self, cache_dir: Path, max_bytes: int, zoom: int):
        self.cache_dir = cache_dir
        self.max_bytes = max_bytes
        self.zoom = zoom
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._mem: OrderedDict[tuple[int, int], object] = OrderedDict()
        self._mem_bytes = 0
        self.hits = 0
        self.misses = 0
        self.fetches = 0

    def _path(self, x: int, y: int) -> Path:
        return self.cache_dir / str(self.zoom) / str(x) / f"{y}.webp"

    def _enforce_limit(self) -> None:
        # Soft limit: delete oldest on-disk tiles under zoom until under budget.
        total = 0
        files: list[tuple[float, Path]] = []
        for p in self.cache_dir.rglob("*.webp"):
            try:
                st = p.stat()
            except OSError:
                continue
            total += st.st_size
            files.append((st.st_mtime, p))
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

    def get_image(self, x: int, y: int):
        from PIL import Image
        import io

        key = (x, y)
        if key in self._mem:
            self._mem.move_to_end(key)
            self.hits += 1
            return self._mem[key]
        path = self._path(x, y)
        data: bytes | None = None
        if path.is_file():
            data = path.read_bytes()
            self.hits += 1
        else:
            self.misses += 1
            url = TILE_TMPL.format(z=self.zoom, x=x, y=y)
            req = urllib.request.Request(
                url, headers={"User-Agent": os.environ.get("NAVI_MWM_HTTP_USER_AGENT", DEFAULT_UA)}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            self.fetches += 1
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self._enforce_limit()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        # Keep a few decoded tiles in RAM.
        approx = img.size[0] * img.size[1] * 3
        self._mem[key] = img
        self._mem_bytes += approx
        while len(self._mem) > 64:
            self._mem.popitem(last=False)
        return img

    def sample(self, lat: float, lon: float) -> float | None:
        x, y = latlon_to_tile(lat, lon, self.zoom)
        img = self.get_image(x, y)
        n = 2.0**self.zoom
        # Pixel within tile (256 assumed; webp terrarium tiles are 512 for some providers —
        # use actual size).
        w, h = img.size
        xf = ((lon + 180.0) / 360.0 * n - x) * w
        # Web mercator Y
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
    ap.add_argument("--zoom", type=int, default=0, help="0 = auto highest")
    ap.add_argument("--stats-out", type=Path, default=None)
    ap.add_argument("--max-features", type=int, default=0)
    args = ap.parse_args()

    # Probe zoom from first feature midpoint.
    if args.zoom <= 0:
        args.zoom = int(os.environ.get("NAVI_MWM_MAPTERHORN_ZOOM", "0") or "0")
    zoom = args.zoom
    with args.roads_jsonl.open() as f:
        first = f.readline()
    if not first:
        args.out.write_text("")
        return 0
    sample = json.loads(first)
    pts = sample["points"]
    mid = pts[len(pts) // 2]
    if zoom <= 0:
        zoom = highest_working_zoom(mid["lat"], mid["lon"], 15)
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

    stats = {
        "zoom": zoom,
        "threshold_m": args.threshold_m,
        "total_points": total_points,
        "override_points": override_points,
        "override_rate": (override_points / total_points) if total_points else 0.0,
        "features_with_overrides": features_out,
        "tile_cache_hits": cache.hits,
        "tile_cache_misses": cache.misses,
        "tile_fetches": cache.fetches,
        "wall_s": time.monotonic() - t0,
    }
    if args.stats_out:
        args.stats_out.write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
