#!/usr/bin/env python3
"""Order bake regions so processing follows the local-night terminator.

At a given UTC start time, local midnight sits on a meridian. As UTC advances,
that midnight meridian moves west with Earth's rotation. Ordering regions from
that meridian westward tends to process each area around its own local night
instead of an arbitrary alphabetical queue.

Usage:
  sun-order-regions.py --bboxes FILE.conf.bboxes.json [--start-unix EPOCH]
  sun-order-regions.py --bboxes FILE --regions-conf FILE.conf
  # reads region ids on stdin (one per line) if --regions-conf omitted

Env:
  NAVI_BAKE_SUN_ORDER=0   disable (identity / input order)
  NAVI_BAKE_START_UNIX    override start instant (seconds UTC)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path


def normalize_lon(lon: float) -> float:
    while lon <= -180.0:
        lon += 360.0
    while lon > 180.0:
        lon -= 360.0
    return lon


def midnight_longitude(start_unix: float) -> float:
    """Longitude where local civil time is ~00:00 at start_unix (UTC)."""
    # Local hours ≈ UTC_hours + lon/15. Set local=0 → lon = -15 * UTC_hours.
    utc_hours = (start_unix % 86400.0) / 3600.0
    return normalize_lon(-15.0 * utc_hours)


def centroid_lon(bbox) -> float | None:
    # bbox: [min_lat, min_lon, max_lat, max_lon]
    if not bbox or len(bbox) != 4:
        return None
    min_lon, max_lon = float(bbox[1]), float(bbox[3])
    # Handle antimeridian-spanning boxes poorly; average then normalize.
    return normalize_lon((min_lon + max_lon) / 2.0)


def west_offset(lon_midnight: float, lon_region: float) -> float:
    """Degrees west of the midnight meridian in [0, 360)."""
    return (lon_midnight - lon_region) % 360.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bboxes", required=True, help="regions.*.bboxes.json")
    ap.add_argument("--regions-conf", default="", help="optional regions.conf to list ids")
    ap.add_argument("--start-unix", type=float, default=None)
    ap.add_argument(
        "--disabled",
        action="store_true",
        help="print input order unchanged (also if NAVI_BAKE_SUN_ORDER=0)",
    )
    args = ap.parse_args()

    enabled = os.environ.get("NAVI_BAKE_SUN_ORDER", "1") != "0" and not args.disabled
    start = args.start_unix
    if start is None:
        env_start = os.environ.get("NAVI_BAKE_START_UNIX", "").strip()
        start = float(env_start) if env_start else None
    if start is None:
        import time

        start = time.time()

    bboxes = json.loads(Path(args.bboxes).read_text(encoding="utf-8"))

    ids: list[str] = []
    if args.regions_conf:
        for line in Path(args.regions_conf).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            ids.append(line.split()[0] if line.split() else "")
        ids = [i for i in ids if i]
    else:
        ids = [ln.strip() for ln in sys.stdin if ln.strip() and not ln.startswith("#")]

    if not enabled:
        for rid in ids:
            print(rid)
        return 0

    lon0 = midnight_longitude(start)
    keyed = []
    for i, rid in enumerate(ids):
        lon = centroid_lon(bboxes.get(rid))
        if lon is None:
            # Unknown bbox: stable tie-break at end of night walk.
            keyed.append((360.0, i, rid, None))
        else:
            keyed.append((west_offset(lon0, lon), i, rid, lon))
    keyed.sort(key=lambda t: (t[0], t[1]))

    # Optional debug on stderr when NAVI_BAKE_SUN_ORDER_DEBUG=1
    if os.environ.get("NAVI_BAKE_SUN_ORDER_DEBUG", "0") == "1":
        print(
            f"# sun-order start_unix={start:.0f} midnight_lon={lon0:.3f} n={len(keyed)}",
            file=sys.stderr,
        )
        for off, _i, rid, lon in keyed[:12]:
            print(f"#  {rid} lon={lon} west_of_midnight={off:.2f}", file=sys.stderr)

    for _off, _i, rid, _lon in keyed:
        print(rid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
