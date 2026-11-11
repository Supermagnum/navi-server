#!/usr/bin/env python3
"""Build a countries.txt + borders overlay matching a leaf set.

Writes:
  <overlay>/borders/*.poly  (symlinks or copies from CoMaps borders)
  <overlay>/countries.txt   (nested tree by country prefix)

Regenerating before every bake avoids the spike-3 stale countries.txt assert.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import defaultdict
from pathlib import Path


def read_leafset(path: Path) -> list[str]:
    out: list[str] = []
    for line in path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def country_key(leaf: str) -> str:
    if leaf.startswith("Norway_"):
        return "Norway"
    if leaf.startswith("Sweden_"):
        return "Sweden"
    if leaf.startswith("Denmark_"):
        return "Denmark"
    if leaf.startswith("Finland_"):
        return "Finland"
    if "_" in leaf:
        return leaf.split("_", 1)[0]
    return "Other"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--leafset", required=True, type=Path)
    ap.add_argument("--borders-dir", required=True, type=Path)
    ap.add_argument("--overlay", required=True, type=Path)
    ap.add_argument("--map-series", default="navi-nordic-pilot")
    ap.add_argument("--version", type=int, default=0, help="countries.txt v field; 0=YYYYMMDD UTC")
    ap.add_argument("--copy", action="store_true", help="copy polys instead of symlink")
    args = ap.parse_args()

    leaves = read_leafset(args.leafset)
    overlay = args.overlay
    borders_out = overlay / "borders"
    if borders_out.exists():
        shutil.rmtree(borders_out)
    borders_out.mkdir(parents=True, exist_ok=True)

    for leaf in leaves:
        src = args.borders_dir / f"{leaf}.poly"
        if not src.is_file():
            raise SystemExit(f"missing border: {src}")
        dst = borders_out / f"{leaf}.poly"
        if args.copy:
            shutil.copy2(src, dst)
        else:
            os.symlink(src.resolve(), dst)

    by_country: dict[str, list[str]] = defaultdict(list)
    for leaf in leaves:
        by_country[country_key(leaf)].append(leaf)

    import datetime as dt

    ver = args.version or int(dt.datetime.now(dt.timezone.utc).strftime("%y%m%d"))
    tree = {
        "id": "Countries",
        "v": ver,
        "map_series": args.map_series,
        "g": [],
    }
    for country in sorted(by_country.keys()):
        kids = []
        for leaf in sorted(by_country[country]):
            kids.append(
                {
                    "id": leaf,
                    "affiliations": [country],
                    "s": 0,
                    "sha1_base64": "",
                }
            )
        tree["g"].append({"id": country, "g": kids})

    (overlay / "countries.txt").write_text(json.dumps(tree, indent=1) + "\n")
    print(f"wrote overlay leaves={len(leaves)} countries.txt={overlay / 'countries.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
