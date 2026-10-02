#!/usr/bin/env python3
"""Build / refresh per-region DEM road-cell lists from a PBF.

Usage:
  ./build-dem-cell-list.py --region RID --pbf PATH --state-dir DIR
  ./build-dem-cell-list.py --region RID --pbf PATH --state-dir DIR --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))
from dem_road_cells import ensure_road_cells  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", required=True)
    ap.add_argument("--pbf", required=True, type=Path)
    ap.add_argument("--state-dir", required=True, type=Path)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if not args.pbf.is_file():
        print(f"FAIL: pbf missing {args.pbf}", file=sys.stderr)
        return 2
    cells, path, rebuilt = ensure_road_cells(
        args.state_dir, args.region, args.pbf, force=args.force
    )
    print(
        f"dem_cells region={args.region} cells={len(cells)} "
        f"rebuilt={int(rebuilt)} cache={path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
