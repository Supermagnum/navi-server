#!/usr/bin/env python3
"""Mapterhorn Terrarium PMTiles (z12) -> SRTMGL1-compatible .hgt (production).

Output: 3601×3601 int16 big-endian, void=-32768, CoMaps stem naming plus
`.SRTMGL1.hgt` symlink. Cache under --out; rebuild a cell only when the
Mapterhorn source fingerprint for that cell changed (recorded in
`.cell-meta.json` beside each `.hgt`).

Cells needed = union of leaf-set border bboxes (or explicit --bbox / --cells).
Attribution: write Mapterhorn attribution.json sources into --attribution-out
(never a generic "elevation data" label).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import struct
import sys
import urllib.request
from pathlib import Path

VOID = -32768
HGT_N = 3601
TILE_Z = 12
CELL_BYTES = HGT_N * HGT_N * 2


def latlon_to_pixel(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2**z
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    n = 2**z
    west = x / n * 360.0 - 180.0
    east = (x + 1) / n * 360.0 - 180.0

    def y_to_lat(ty: float) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * ty / n))))

    return west, y_to_lat(y + 1), east, y_to_lat(y)


def terrarium_elev(r: int, g: int, b: int) -> int:
    h = (r * 256.0 + g + b / 256.0) - 32768.0
    if h <= -32768:
        return VOID
    if h >= 32767:
        return 32767
    return int(round(h))


def hgt_stem(south: int, west: int) -> str:
    ns = "N" if south >= 0 else "S"
    ew = "E" if west >= 0 else "W"
    return f"{ns}{abs(south):02d}{ew}{abs(west):03d}"


def poly_bbox(path: Path) -> tuple[float, float, float, float]:
    min_lon = min_lat = float("inf")
    max_lon = max_lat = float("-inf")
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        min_lon = min(min_lon, lon)
        max_lon = max(max_lon, lon)
        min_lat = min(min_lat, lat)
        max_lat = max(max_lat, lat)
    return min_lon, min_lat, max_lon, max_lat


def read_leafset(path: Path) -> list[str]:
    out: list[str] = []
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def cells_for_bbox(min_lat: float, min_lon: float, max_lat: float, max_lon: float) -> set[tuple[int, int]]:
    wanted: set[tuple[int, int]] = set()
    for lat in range(int(math.floor(min_lat)), int(math.floor(max_lat)) + 1):
        for lon in range(int(math.floor(min_lon)), int(math.floor(max_lon)) + 1):
            wanted.add((lat, lon))
    return wanted


def cells_for_leafset(leafset: Path, borders_dir: Path) -> set[tuple[int, int]]:
    wanted: set[tuple[int, int]] = set()
    for leaf in read_leafset(leafset):
        poly = borders_dir / f"{leaf}.poly"
        if not poly.is_file():
            raise SystemExit(f"missing border for cell union: {poly}")
        w, s, e, n = poly_bbox(poly)
        wanted |= cells_for_bbox(s, w, n, e)
    return wanted


def list_z12_tiles(min_lon: float, min_lat: float, max_lon: float, max_lat: float):
    x0, y1 = latlon_to_pixel(min_lat, min_lon, TILE_Z)
    x1, y0 = latlon_to_pixel(max_lat, max_lon, TILE_Z)
    tiles = []
    for x in range(int(math.floor(x0)), int(math.floor(x1)) + 1):
        for y in range(int(math.floor(y0)), int(math.floor(y1)) + 1):
            tiles.append((x, y))
    return tiles


def decode_webp(raw: bytes):
    from PIL import Image

    if not raw or len(raw) < 12 or raw[:4] != b"RIFF":
        return None
    try:
        return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return None


def paint_tile(img, tx, ty, grids, wanted):
    west, south, east, north = tile_bounds(TILE_Z, tx, ty)
    w, h = img.size
    px = img.load()
    for south_i, west_i in wanted:
        if east <= west_i or west >= west_i + 1 or north <= south_i or south >= south_i + 1:
            continue
        buf = grids[(south_i, west_i)]
        row_start = max(0, int(math.floor((south_i + 1 - min(north, south_i + 1)) * (HGT_N - 1))))
        row_end = min(HGT_N - 1, int(math.ceil((south_i + 1 - max(south, south_i)) * (HGT_N - 1))))
        col_start = max(0, int(math.floor((max(west, west_i) - west_i) * (HGT_N - 1))))
        col_end = min(HGT_N - 1, int(math.ceil((min(east, west_i + 1) - west_i) * (HGT_N - 1))))
        for row in range(row_start, row_end + 1):
            lat = (south_i + 1) - row / (HGT_N - 1)
            for col in range(col_start, col_end + 1):
                lon = west_i + col / (HGT_N - 1)
                gx, gy = latlon_to_pixel(lat, lon, TILE_Z)
                ix = int((gx - tx) * w)
                iy = int((gy - ty) * h)
                if ix < 0 or iy < 0 or ix >= w or iy >= h:
                    continue
                r, g, b = px[ix, iy]
                elev = terrarium_elev(r, g, b)
                struct.pack_into(">h", buf, (row * HGT_N + col) * 2, elev)


def source_fingerprint(pmtiles: Path | None, void_fill: bool) -> str:
    if void_fill or pmtiles is None:
        return "void-fill"
    h = hashlib.sha256()
    h.update(str(pmtiles.resolve()).encode())
    h.update(str(pmtiles.stat().st_size).encode())
    h.update(str(int(pmtiles.stat().st_mtime_ns)).encode())
    return h.hexdigest()


def cell_meta_path(out: Path, stem: str) -> Path:
    return out / f"{stem}.cell-meta.json"


def needs_rebuild(out: Path, stem: str, fp: str) -> bool:
    hgt = out / f"{stem}.hgt"
    meta = cell_meta_path(out, stem)
    if not hgt.is_file() or hgt.stat().st_size != CELL_BYTES:
        return True
    if not meta.is_file():
        return True
    try:
        data = json.loads(meta.read_text())
    except json.JSONDecodeError:
        return True
    return data.get("source_fingerprint") != fp


def write_void_cell(out: Path, south: int, west: int, fp: str, fill_value: int = VOID) -> None:
    """Write a filler cell. Uses hardlinks to a shared template to avoid ~25MiB×N disk."""
    stem = hgt_stem(south, west)
    path = out / f"{stem}.hgt"
    template = out / (".zero-template.hgt" if fill_value == 0 else ".void-template.hgt")
    if not template.is_file() or template.stat().st_size != CELL_BYTES:
        template.write_bytes(struct.pack(">h", fill_value) * (HGT_N * HGT_N))
    if path.exists() or path.is_symlink():
        path.unlink()
    try:
        os.link(template, path)
    except OSError:
        # Cross-device or link limit — fall back to copyfile
        import shutil

        shutil.copyfile(template, path)
    alt = out / f"{stem}.SRTMGL1.hgt"
    if alt.exists() or alt.is_symlink():
        alt.unlink()
    alt.symlink_to(path.name)
    cell_meta_path(out, stem).write_text(
        json.dumps(
            {
                "stem": stem,
                "south": south,
                "west": west,
                "source_fingerprint": fp,
                "void_fill": fill_value == VOID,
                "zero_fill": fill_value == 0,
                "hardlink_template": True,
            },
            indent=2,
        )
        + "\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pmtiles", type=Path, default=None)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--attribution-out", required=True, type=Path)
    ap.add_argument("--attribution-url", default="https://download.mapterhorn.com/attribution.json")
    ap.add_argument("--attribution-json", type=Path, default=None, help="local attribution.json cache")
    ap.add_argument("--leafset", type=Path, default=None)
    ap.add_argument("--borders-dir", type=Path, default=None)
    ap.add_argument("--bbox", default=None, help="min_lon,min_lat,max_lon,max_lat")
    ap.add_argument(
        "--allow-void-fill",
        action="store_true",
        help="Write filler cells when --pmtiles missing or tile uncovered (scratch/e2e)",
    )
    ap.add_argument(
        "--zero-fill",
        action="store_true",
        help="Filler cells use elevation 0 instead of SRTM void (-32768). "
        "CoMaps skips altitudes sections when all samples are void; zero-fill is for scratch e2e only.",
    )
    ap.add_argument(
        "--keep-existing",
        action="store_true",
        help="Do not rebuild cells that already have a full-size .hgt (ignore fingerprint)",
    )
    args = ap.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    if args.leafset and args.borders_dir:
        wanted = cells_for_leafset(args.leafset, args.borders_dir)
        bbox_label = "leafset-union"
        bbox_vals = None
    elif args.bbox:
        min_lon, min_lat, max_lon, max_lat = [float(x) for x in args.bbox.split(",")]
        wanted = cells_for_bbox(min_lat, min_lon, max_lat, max_lon)
        bbox_label = "explicit"
        bbox_vals = [min_lon, min_lat, max_lon, max_lat]
    else:
        print("need --leafset+--borders-dir or --bbox", file=sys.stderr)
        return 2

    void_fill = bool(args.allow_void_fill) or args.pmtiles is None
    if args.pmtiles is None and not args.allow_void_fill:
        print("ERROR: --pmtiles required unless --allow-void-fill", file=sys.stderr)
        return 2

    fp = source_fingerprint(args.pmtiles, void_fill and args.pmtiles is None)
    pending = set()
    for s, w in wanted:
        stem = hgt_stem(s, w)
        hgt = out / f"{stem}.hgt"
        if args.keep_existing and hgt.is_file() and hgt.stat().st_size == CELL_BYTES:
            continue
        if needs_rebuild(out, stem, fp):
            pending.add((s, w))
    print(f"cells total={len(wanted)} pending={len(pending)} fp={fp[:12]}…", flush=True)

    if pending and args.pmtiles is not None:
        from pmtiles.reader import MmapSource, Reader

        grids = {k: bytearray(struct.pack(">h", VOID) * (HGT_N * HGT_N)) for k in pending}
        tiles = list_z12_tiles(
            min(w for _, w in pending),
            min(s for s, _ in pending),
            max(w for _, w in pending) + 1,
            max(s for s, _ in pending) + 1,
        )
        print(f"painting {len(tiles)} z12 tiles from {args.pmtiles}", flush=True)
        with open(args.pmtiles, "rb") as f:
            reader = Reader(MmapSource(f))
            ok = 0
            for i, (tx, ty) in enumerate(tiles):
                raw = reader.get(TILE_Z, tx, ty)
                if not raw:
                    continue
                img = decode_webp(raw)
                if img is None:
                    continue
                paint_tile(img, tx, ty, grids, pending)
                ok += 1
                if i % 500 == 0:
                    print(f"  {i}/{len(tiles)} ok={ok}", flush=True)
        for (south, west), buf in sorted(grids.items()):
            stem = hgt_stem(south, west)
            path = out / f"{stem}.hgt"
            path.write_bytes(buf)
            alt = out / f"{stem}.SRTMGL1.hgt"
            if alt.exists() or alt.is_symlink():
                alt.unlink()
            alt.symlink_to(path.name)
            cell_meta_path(out, stem).write_text(
                json.dumps(
                    {
                        "stem": stem,
                        "south": south,
                        "west": west,
                        "source_fingerprint": fp,
                        "pmtiles": str(args.pmtiles.resolve()),
                        "void_fill": False,
                    },
                    indent=2,
                )
                + "\n"
            )
            print(f"wrote {path}", flush=True)
    elif pending and void_fill:
        fill_value = 0 if args.zero_fill else VOID
        print(f"filling {len(pending)} cells fill_value={fill_value}", flush=True)
        for s, w in sorted(pending):
            write_void_cell(out, s, w, fp, fill_value=fill_value)

    # Attribution: never generic "elevation data"
    attr: list | dict | None = None
    if args.attribution_json and args.attribution_json.is_file():
        attr = json.loads(args.attribution_json.read_text())
    else:
        try:
            with urllib.request.urlopen(args.attribution_url, timeout=60) as resp:
                attr = json.loads(resp.read().decode())
        except Exception as e:
            print(f"WARNING: could not fetch attribution: {e}", file=sys.stderr)
            attr = {
                "error": str(e),
                "attribution_url": args.attribution_url,
                "note": "Mapterhorn sources required; do not substitute generic elevation label",
            }

    args.attribution_out.parent.mkdir(parents=True, exist_ok=True)
    args.attribution_out.write_text(
        json.dumps(
            {
                "label": "Mapterhorn",
                "attribution_url": args.attribution_url,
                "sources": attr,
            },
            indent=2,
        )
        + "\n"
    )

    results = []
    for s, w in sorted(wanted):
        stem = hgt_stem(s, w)
        path = out / f"{stem}.hgt"
        results.append({"file": stem + ".hgt", "bytes": path.stat().st_size, "south": s, "west": w})

    man = {
        "area": bbox_label,
        "bbox": bbox_vals,
        "source": "Mapterhorn Terrarium PMTiles z12",
        "label": "Mapterhorn",
        "pmtiles": str(args.pmtiles) if args.pmtiles else None,
        "hgt_spec": "3601x3601 int16 BE, void=-32768",
        "cells": len(results),
        "tiles": results,
        "attribution_file": str(args.attribution_out),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(man, indent=2) + "\n")
    print("manifest", args.manifest, "n=", len(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
