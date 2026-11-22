#!/usr/bin/env python3
"""Clip a named-routes PBF to a leaf and export NR01 JSONL via place-source-points.

Prefers ``osmium extract -p`` when ``--poly`` is given. If the extract is empty
(common when the source PBF still has duplicate object versions from merged
extracts), falls back to exporting the full PBF with an in-process ``--bbox``
filter derived from the poly.

Binary resolution: ``NAVI_PLACE_SOURCE_BIN``, else
``<repo>/target/release/place-source-points``.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent


def _place_source_bin() -> Path:
    env = os.environ.get("NAVI_PLACE_SOURCE_BIN", "").strip()
    if env:
        return Path(env)
    return REPO_ROOT / "target" / "release" / "place-source-points"


def _have_osmium() -> bool:
    return shutil.which("osmium") is not None


def _poly_bbox(path: Path) -> tuple[float, float, float, float]:
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
    if min_lon == float("inf"):
        raise SystemExit(f"no coordinates in poly: {path}")
    return min_lon, min_lat, max_lon, max_lat


def _pbf_object_count(path: Path) -> int:
    if not path.is_file() or path.stat().st_size < 200:
        return 0
    try:
        out = subprocess.check_output(
            ["osmium", "fileinfo", "-e", str(path)], text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return 0
    total = 0
    for line in out.splitlines():
        if "Number of nodes:" in line or "Number of ways:" in line or "Number of relations:" in line:
            total += int(line.rsplit(":", 1)[-1].strip())
    return total


def _export(bin_path: Path, pbf: Path, out_jsonl: Path, bbox: str | None) -> None:
    cmd = [
        str(bin_path),
        "export-named-routes",
        "--input",
        str(pbf),
        "--output",
        str(out_jsonl),
    ]
    if bbox:
        cmd.extend(["--bbox", bbox])
    subprocess.check_call(cmd)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pbf", type=Path, required=True, help="Named-routes (or planet) .osm.pbf")
    ap.add_argument("--poly", type=Path, default=None, help="Leaf .poly for osmium extract")
    ap.add_argument(
        "--bbox",
        type=str,
        default=None,
        help="west,south,east,north when --poly is absent",
    )
    ap.add_argument("--out-jsonl", type=Path, required=True)
    ap.add_argument("--report-json", type=Path, required=True)
    args = ap.parse_args()

    if not args.pbf.is_file():
        raise SystemExit(f"pbf missing: {args.pbf}")
    # Guard against empty/tiny PBFs from a broken dedupe/merge (full planet
    # routes extract is ~100+ MiB; anything under 1 MiB is not usable).
    min_bytes = int(os.environ.get("NAVI_MWM_NAMED_ROUTES_MIN_BYTES", "1000000"))
    pbf_bytes = args.pbf.stat().st_size
    if pbf_bytes < min_bytes:
        raise SystemExit(
            f"named-routes PBF too small ({pbf_bytes} < {min_bytes} bytes): "
            f"{args.pbf}"
        )
    bin_path = _place_source_bin()
    if not bin_path.is_file():
        raise SystemExit(
            f"place-source-points missing: {bin_path} "
            "(set NAVI_PLACE_SOURCE_BIN or cargo build -p place-source-points --release)"
        )

    args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    args.report_json.parent.mkdir(parents=True, exist_ok=True)

    work_pbf = args.pbf
    bbox_str: str | None = args.bbox
    td_ctx = None
    used_fallback = False
    try:
        if args.poly is not None and args.poly.is_file():
            west, south, east, north = _poly_bbox(args.poly)
            bbox_str = f"{west},{south},{east},{north}"
            if _have_osmium():
                td_ctx = tempfile.TemporaryDirectory(prefix="navi-nr-")
                clipped = Path(td_ctx.name) / "clip.pbf"
                try:
                    subprocess.check_call(
                        [
                            "osmium",
                            "extract",
                            "-p",
                            str(args.poly),
                            "-o",
                            str(clipped),
                            "--overwrite",
                            str(args.pbf),
                        ],
                        stderr=subprocess.DEVNULL,
                    )
                except subprocess.CalledProcessError:
                    clipped = Path(td_ctx.name) / "clip-empty.pbf"
                if _pbf_object_count(clipped) > 0:
                    work_pbf = clipped
                    bbox_str = None  # already clipped
                else:
                    # Duplicate-object source PBF: filter in place-source instead.
                    used_fallback = True
                    work_pbf = args.pbf
        elif args.bbox:
            bbox_str = args.bbox
            if _have_osmium():
                parts = [float(x) for x in args.bbox.split(",")]
                if len(parts) != 4:
                    raise SystemExit("--bbox needs west,south,east,north")
                west, south, east, north = parts
                td_ctx = tempfile.TemporaryDirectory(prefix="navi-nr-")
                clipped = Path(td_ctx.name) / "clip.pbf"
                try:
                    subprocess.check_call(
                        [
                            "osmium",
                            "extract",
                            f"--bbox={west},{south},{east},{north}",
                            "-o",
                            str(clipped),
                            "--overwrite",
                            str(args.pbf),
                        ],
                        stderr=subprocess.DEVNULL,
                    )
                except subprocess.CalledProcessError:
                    clipped = Path(td_ctx.name) / "clip-empty.pbf"
                if _pbf_object_count(clipped) > 0:
                    work_pbf = clipped
                    bbox_str = None
                else:
                    used_fallback = True
                    work_pbf = args.pbf

        _export(bin_path, work_pbf, args.out_jsonl, bbox_str)
    finally:
        if td_ctx is not None:
            td_ctx.cleanup()

    by_kind: Counter[str] = Counter()
    routes = 0
    if args.out_jsonl.is_file():
        for line in args.out_jsonl.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            kind = str(row.get("kind") or "other")
            by_kind[kind] += 1
            routes += 1

    report = {
        "routes": routes,
        "by_kind": dict(sorted(by_kind.items())),
        "bbox_fallback": used_fallback,
    }
    args.report_json.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
