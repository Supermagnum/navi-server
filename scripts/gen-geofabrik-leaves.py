#!/usr/bin/env python3
"""Generate regions.conf covering all Geofabrik leaf extracts (world coverage).

Uses https://download.geofabrik.de/index-v1.json. Leaves = features with a PBF
URL and no children in the parent/child tree. Avoids continent blobs.

Usage:
  ./gen-geofabrik-leaves.py -o /media/navi/navi-server/data/regions.planet.conf
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path


INDEX_URL = "https://download.geofabrik.de/index-v1.json"


def region_id_from_path(path: str) -> str:
    # europe/norway/ostlandet -> europe_norway_ostlandet
    return re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--index", default=INDEX_URL)
    args = ap.parse_args()

    if args.index.startswith("http"):
        with urllib.request.urlopen(args.index, timeout=120) as resp:
            idx = json.load(resp)
    else:
        idx = json.loads(Path(args.index).read_text(encoding="utf-8"))

    by_id = {}
    children: dict[str, list[str]] = defaultdict(list)
    for feat in idx["features"]:
        props = feat["properties"]
        rid = props["id"]
        by_id[rid] = (props, feat.get("geometry"))
        parent = props.get("parent")
        if parent:
            children[parent].append(rid)

    lines = [
        "# Auto-generated Geofabrik leaf regions (world coverage).",
        "# Do not edit by hand — regenerate with scripts/gen-geofabrik-leaves.py",
        "",
    ]
    bboxes = {}
    count = 0
    for rid, (props, geom) in sorted(by_id.items(), key=lambda kv: kv[0]):
        urls = props.get("urls") or {}
        pbf = urls.get("pbf")
        if not pbf:
            continue
        if children.get(rid):
            continue
        path = pbf.replace("https://download.geofabrik.de/", "").replace(
            "-latest.osm.pbf", ""
        )
        region_id = region_id_from_path(path)
        lines.append(f"{region_id}\tgeofabrik:{path}")
        # bbox from geometry if present
        if geom and geom.get("type") == "Polygon":
            coords = geom["coordinates"][0]
            lons = [c[0] for c in coords]
            lats = [c[1] for c in coords]
            bboxes[region_id] = [min(lats), min(lons), max(lats), max(lons)]
        elif geom and geom.get("type") == "MultiPolygon":
            lats, lons = [], []
            for poly in geom["coordinates"]:
                for c in poly[0]:
                    lons.append(c[0])
                    lats.append(c[1])
            bboxes[region_id] = [min(lats), min(lons), max(lats), max(lons)]
        count += 1

    out = Path(args.output)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bbox_path = out.with_suffix(out.suffix + ".bboxes.json")
    bbox_path.write_text(json.dumps(bboxes, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {count} regions -> {out}", file=sys.stderr)
    print(f"wrote bboxes -> {bbox_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
