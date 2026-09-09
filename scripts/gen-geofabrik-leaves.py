#!/usr/bin/env python3
"""Generate regions.conf covering Geofabrik leaf extracts (world coverage)
plus OSM.fr supplements where Geofabrik has no sub-extracts.

Uses https://download.geofabrik.de/index-v1.json. Leaves = features with a PBF
URL and no children in the parent/child tree. Avoids continent blobs.

Sweden: Geofabrik publishes only country-level europe/sweden (no lan). OSM.fr
publishes all 21 lan under extracts/europe/sweden/<lan>-latest.osm.pbf — those
are emitted as url: leaves and the Geofabrik sweden country leaf is omitted so
planet-leaves does not double-bake.

Usage:
  ./gen-geofabrik-leaves.py -o /media/navi/navi-server/data/regions.planet.conf
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from html.parser import HTMLParser
from pathlib import Path


INDEX_URL = "https://download.geofabrik.de/index-v1.json"
OSMFR_SWEDEN_DIR = "https://download.openstreetmap.fr/extracts/europe/sweden/"
OSMFR_POLY_BASE = "https://download.openstreetmap.fr/polygons"


def region_id_from_path(path: str) -> str:
    # europe/norway/ostlandet -> europe_norway_ostlandet
    return re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")


class _HrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for k, v in attrs:
            if k == "href" and v:
                self.hrefs.append(v)


def list_osmfr_sweden_lan() -> list[str]:
    """Return sorted lan slugs that have *-latest.osm.pbf on OSM.fr."""
    with urllib.request.urlopen(OSMFR_SWEDEN_DIR, timeout=60) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    parser = _HrefParser()
    parser.feed(html)
    lans: set[str] = set()
    for href in parser.hrefs:
        m = re.fullmatch(r"([a-z0-9_]+)-latest\.osm\.pbf", href.split("/")[-1])
        if m:
            lans.add(m.group(1))
    if len(lans) < 21:
        raise SystemExit(
            f"expected 21 Swedish lan on OSM.fr, found {len(lans)}: {sorted(lans)}"
        )
    return sorted(lans)


def bbox_from_poly_url(url: str) -> list[float] | None:
    """Parse Osmosis .poly -> [min_lat, min_lon, max_lat, max_lon]."""
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None
    lats: list[float] = []
    lons: list[float] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        if -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0:
            lons.append(lon)
            lats.append(lat)
    if not lats:
        return None
    return [min(lats), min(lons), max(lats), max(lons)]


def sweden_osmfr_leaves() -> tuple[list[str], dict[str, list[float]]]:
    """Return (conf lines, bboxes) for OSM.fr Sweden lan."""
    lans = list_osmfr_sweden_lan()
    lines: list[str] = []
    bboxes: dict[str, list[float]] = {}

    def one(lan: str) -> tuple[str, str, list[float] | None]:
        path = f"europe/sweden/{lan}"
        rid = region_id_from_path(path)
        url = f"{OSMFR_SWEDEN_DIR}{lan}-latest.osm.pbf"
        line = f"{rid}\turl:{url}"
        bbox = bbox_from_poly_url(f"{OSMFR_POLY_BASE}/{path}.poly")
        return rid, line, bbox

    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        for rid, line, bbox in ex.map(one, lans):
            lines.append(line)
            if bbox:
                bboxes[rid] = bbox
    lines.sort()
    return lines, bboxes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--index", default=INDEX_URL)
    ap.add_argument(
        "--no-sweden-lan",
        action="store_true",
        help="omit OSM.fr Sweden lan supplement (keep Geofabrik country leaf)",
    )
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

    use_sweden_lan = not args.no_sweden_lan
    sweden_lines: list[str] = []
    sweden_bboxes: dict[str, list[float]] = {}
    if use_sweden_lan:
        sweden_lines, sweden_bboxes = sweden_osmfr_leaves()
        print(
            f"OSM.fr Sweden lan: {len(sweden_lines)} (omit Geofabrik europe/sweden)",
            file=sys.stderr,
        )

    lines = [
        "# Auto-generated Geofabrik leaf regions (world coverage).",
        "# Sweden lan: OSM.fr extracts (Geofabrik has no lan breakdown).",
        "# Regenerate: scripts/gen-geofabrik-leaves.py -o data/regions.planet.conf",
        "",
    ]
    bboxes: dict[str, list[float]] = {}
    count = 0
    skipped_sweden = 0
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
        # Country-level Sweden is replaced by OSM.fr lan leaves.
        if use_sweden_lan and path == "europe/sweden":
            skipped_sweden += 1
            continue
        region_id = region_id_from_path(path)
        lines.append(f"{region_id}\tgeofabrik:{path}")
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

    if sweden_lines:
        lines.append("")
        lines.append("# --- OSM.fr Sweden lan (Geofabrik has country only) ---")
        lines.extend(sweden_lines)
        bboxes.update(sweden_bboxes)
        count += len(sweden_lines)

    out = Path(args.output)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bbox_path = out.with_suffix(out.suffix + ".bboxes.json")
    bbox_path.write_text(
        json.dumps(bboxes, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"wrote {count} regions -> {out} (skipped_geofabrik_sweden={skipped_sweden})",
        file=sys.stderr,
    )
    print(f"wrote bboxes -> {bbox_path} (n={len(bboxes)})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
