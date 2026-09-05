#!/usr/bin/env python3
"""Prefetch / lease / evict Copernicus DEM 30m tiles for a bbox.

Layout (matches Navi ElevationCache):
  elev_dir/copernicus/<NxxExxx>/Copernicus_DSM_COG_10_….tif

Concurrency safety:
  elev_dir/.tile_locks/<stem>.lock     — flock around download
  elev_dir/.tile_leases/<stem>/<id>    — one file per active lease; refcount=file count
  Evict only removes tiles whose lease dir is empty (refcount 0).

Ocean-skip:
  --poly PATH  Osmosis/Geofabrik .poly for the extract. 1-degree cells with
               no intersection are skipped before HTTP. Missing/invalid poly
               => fail-open (fetch all bbox cells).
  elev_dir/copernicus_ocean_404.txt  persistent negative cache of stems that
               previously 404'd (survives scratch cleanup).

Usage:
  ./prefetch-dem-bbox.py --elev-dir DIR --bbox=min_lat,min_lon,max_lat,max_lon
  ./prefetch-dem-bbox.py --elev-dir DIR --bbox=… --poly /path/to/region.poly
  ./prefetch-dem-bbox.py --elev-dir DIR --bbox=… --lease-id REGION-PID
  ./prefetch-dem-bbox.py --elev-dir DIR --bbox=… --lease-release --lease-id …
  ./prefetch-dem-bbox.py --elev-dir DIR --bbox=… --evict
"""

from __future__ import annotations

import argparse
import fcntl
import math
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))
from dem_ocean_404_cache import load_missing, remember_missing  # noqa: E402
from dem_poly_filter import classify_bbox_tiles, try_load_poly  # noqa: E402

BUCKET = "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com"


def tile_stem(lat_floor: int, lon_floor: int) -> str:
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    return f"{ns}{abs(lat_floor):02d}{ew}{abs(lon_floor):03d}"


def copernicus_prefix(lat_floor: int, lon_floor: int) -> str:
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    return (
        f"Copernicus_DSM_COG_10_{ns}{abs(lat_floor):02d}_00_"
        f"{ew}{abs(lon_floor):03d}_00_DEM"
    )


def bbox_tiles(min_lat: float, min_lon: float, max_lat: float, max_lon: float):
    for lat in range(math.floor(min_lat), math.floor(max_lat) + 1):
        for lon in range(math.floor(min_lon), math.floor(max_lon) + 1):
            yield lat, lon


def tile_dest(elev: Path, lat_floor: int, lon_floor: int) -> Path:
    stem = tile_stem(lat_floor, lon_floor)
    prefix = copernicus_prefix(lat_floor, lon_floor)
    return elev / "copernicus" / stem / f"{prefix}.tif"


def lease_dir(elev: Path, stem: str) -> Path:
    return elev / ".tile_leases" / stem


def lock_path(elev: Path, stem: str) -> Path:
    return elev / ".tile_locks" / f"{stem}.lock"


def lease_count(elev: Path, stem: str) -> int:
    d = lease_dir(elev, stem)
    if not d.is_dir():
        return 0
    return sum(1 for p in d.iterdir() if p.is_file())


def acquire_lease(elev: Path, stem: str, lease_id: str) -> None:
    d = lease_dir(elev, stem)
    d.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-+" else "_" for c in lease_id)
    (d / safe).write_text(f"{time.time():.3f}\n", encoding="utf-8")


def release_lease(elev: Path, stem: str, lease_id: str) -> bool:
    safe = "".join(c if c.isalnum() or c in "._-+" else "_" for c in lease_id)
    p = lease_dir(elev, stem) / safe
    if p.is_file():
        p.unlink(missing_ok=True)
        d = lease_dir(elev, stem)
        try:
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass
        return True
    return False


def release_leases_for_bbox(
    elev: Path, tiles: list[tuple[int, int]], lease_id: str
) -> int:
    n = 0
    for lat, lon in tiles:
        if release_lease(elev, tile_stem(lat, lon), lease_id):
            n += 1
    return n


def evict_bbox(
    elev: Path, min_lat: float, min_lon: float, max_lat: float, max_lon: float
) -> tuple[int, int]:
    """Delete cached DEM tiles with zero leases. Returns (removed, skipped_leased)."""
    removed = 0
    skipped = 0
    for lat, lon in bbox_tiles(min_lat, min_lon, max_lat, max_lon):
        stem = tile_stem(lat, lon)
        if lease_count(elev, stem) > 0:
            skipped += 1
            continue
        d = elev / "copernicus" / stem
        if not d.exists():
            continue
        shutil.rmtree(d, ignore_errors=True)
        ld = lease_dir(elev, stem)
        if ld.is_dir():
            shutil.rmtree(ld, ignore_errors=True)
        removed += 1
    return removed, skipped


def download_locked(elev: Path, stem: str, url: str, dest: Path) -> bool:
    """Download with exclusive flock so concurrent workers share one fetch."""
    lock_p = lock_path(elev, stem)
    lock_p.parent.mkdir(parents=True, exist_ok=True)
    with lock_p.open("a+", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        if dest.is_file() and dest.stat().st_size > 0:
            return True
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_suffix(dest.suffix + f".partial.{os.getpid()}")
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "navi-server-smoke/1.0"}
            )
            with urllib.request.urlopen(req, timeout=300) as resp, partial.open("wb") as out:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
            if dest.is_file() and dest.stat().st_size > 0:
                partial.unlink(missing_ok=True)
                return True
            partial.replace(dest)
            return True
        except urllib.error.HTTPError as e:
            partial.unlink(missing_ok=True)
            if e.code == 404:
                return False
            raise
        except Exception:
            partial.unlink(missing_ok=True)
            raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--elev-dir", required=True)
    ap.add_argument("--bbox", required=True, help="min_lat,min_lon,max_lat,max_lon")
    ap.add_argument(
        "--poly",
        default="",
        help="Osmosis/Geofabrik .poly — skip DEM cells outside extract (fail-open)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="classify only; no downloads (prints skip/fetch counts)",
    )
    ap.add_argument(
        "--max-tiles",
        type=int,
        default=2500,
        help="abort prefetch if bbox needs more cells (default 2500)",
    )
    ap.add_argument(
        "--lease-id",
        default="",
        help="acquire/release id (e.g. region-pid); required for --lease-release",
    )
    ap.add_argument(
        "--lease-release",
        action="store_true",
        help="drop leases for --lease-id over this bbox (no download)",
    )
    ap.add_argument(
        "--evict",
        action="store_true",
        help="delete cached tiles with zero leases for this bbox",
    )
    args = ap.parse_args()
    parts = [float(x) for x in args.bbox.split(",")]
    if len(parts) != 4:
        print("bbox must be min_lat,min_lon,max_lat,max_lon", file=sys.stderr)
        return 2
    min_lat, min_lon, max_lat, max_lon = parts
    elev = Path(args.elev_dir)
    tiles = list(bbox_tiles(min_lat, min_lon, max_lat, max_lon))

    if args.lease_release:
        if not args.lease_id:
            print("--lease-release requires --lease-id", file=sys.stderr)
            return 2
        n = release_leases_for_bbox(elev, tiles, args.lease_id)
        print(f"dem_lease_release released={n} lease_id={args.lease_id}")
        return 0

    if args.evict:
        removed, skipped = evict_bbox(elev, min_lat, min_lon, max_lat, max_lon)
        print(
            f"dem_evict removed={removed} skipped_leased={skipped} bbox_cells={len(tiles)}"
        )
        return 0

    if len(tiles) > args.max_tiles:
        print(
            f"FAIL: bbox needs {len(tiles)} tiles > max {args.max_tiles}",
            file=sys.stderr,
        )
        return 3

    ocean_skip = 0
    fetch_tiles = tiles
    if args.poly:
        rings = try_load_poly(args.poly)
        if rings is None:
            print(
                "dem_ocean_filter fail_open=1 reason=poly_unusable "
                f"poly={args.poly} bbox_tiles={len(tiles)}"
            )
        else:
            fetch_tiles, skipped = classify_bbox_tiles(tiles, rings)
            ocean_skip = len(skipped)
            print(
                "dem_ocean_filter "
                f"bbox_tiles={len(tiles)} fetch={len(fetch_tiles)} "
                f"ocean_skip={ocean_skip} poly={args.poly}"
            )

    known_404 = load_missing(elev)
    if args.dry_run:
        neg = sum(1 for lat, lon in fetch_tiles if tile_stem(lat, lon) in known_404)
        print(
            f"dem_prefetch dry_run=1 ok=0 cached=0 miss=0 ocean_skip={ocean_skip} "
            f"neg_cache_hit={neg} fetch={len(fetch_tiles)} total={len(tiles)}"
        )
        return 0

    ok = cached = miss = neg_hit = 0
    new_404: list[str] = []
    for lat, lon in fetch_tiles:
        stem = tile_stem(lat, lon)
        if args.lease_id:
            acquire_lease(elev, stem, args.lease_id)
        dest = tile_dest(elev, lat, lon)
        if dest.is_file() and dest.stat().st_size > 0:
            cached += 1
            continue
        if stem in known_404:
            neg_hit += 1
            continue
        prefix = copernicus_prefix(lat, lon)
        url = f"{BUCKET}/{prefix}/{prefix}.tif"
        print(f"fetch {stem} …", flush=True)
        if download_locked(elev, stem, url, dest):
            ok += 1
        else:
            miss += 1
            new_404.append(stem)
            known_404.add(stem)
            print(f"  miss (404) {stem}", flush=True)

    if new_404:
        remember_missing(elev, new_404)

    lease_note = f" lease_id={args.lease_id}" if args.lease_id else ""
    print(
        f"dem_prefetch ok={ok} cached={cached} miss={miss} ocean_skip={ocean_skip} "
        f"neg_cache_hit={neg_hit} fetch={len(fetch_tiles)} total={len(tiles)}{lease_note}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
