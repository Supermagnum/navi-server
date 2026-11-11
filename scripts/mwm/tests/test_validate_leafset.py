#!/usr/bin/env python3
"""Unit tests for validate-leafset.py and prepare-countries-overlay.py."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VALIDATE = ROOT / "scripts/mwm/validate-leafset.py"
PREPARE = ROOT / "scripts/mwm/prepare-countries-overlay.py"


def write_poly(path: Path, west: float, south: float, east: float, north: float) -> None:
    path.write_text(
        f"test\n"
        f"1\n"
        f"  {west} {south}\n"
        f"  {east} {south}\n"
        f"  {east} {north}\n"
        f"  {west} {north}\n"
        f"  {west} {south}\n"
        f"END\nEND\n"
    )


class LeafsetTests(unittest.TestCase):
    def test_ok_matching_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            borders = t / "borders"
            borders.mkdir()
            write_poly(borders / "Norway_Hedmark.poly", 10, 60, 12, 62)
            write_poly(borders / "Norway_Oppland.poly", 8, 60, 11, 62)
            write_poly(borders / "Germany_Extra.poly", 5, 50, 6, 51)
            leafset = t / "leaves.txt"
            leafset.write_text("Norway_Hedmark\nNorway_Oppland\n")
            overlay = t / "overlay"
            subprocess.check_call(
                [
                    "python3",
                    str(PREPARE),
                    "--leafset",
                    str(leafset),
                    "--borders-dir",
                    str(borders),
                    "--overlay",
                    str(overlay),
                    "--copy",
                ]
            )
            subprocess.check_call(
                [
                    "python3",
                    str(VALIDATE),
                    "--leafset",
                    str(leafset),
                    "--borders-dir",
                    str(borders),
                    "--overlay-borders",
                    str(overlay / "borders"),
                    "--overlay-countries",
                    str(overlay / "countries.txt"),
                ]
            )

    def test_leftover_border_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            borders = t / "borders"
            borders.mkdir()
            write_poly(borders / "Norway_Hedmark.poly", 10, 60, 12, 62)
            write_poly(borders / "Sweden_Stockholm.poly", 17, 59, 19, 60)
            leafset = t / "leaves.txt"
            leafset.write_text("Norway_Hedmark\n")
            overlay_b = t / "overlay" / "borders"
            overlay_b.mkdir(parents=True)
            write_poly(overlay_b / "Norway_Hedmark.poly", 10, 60, 12, 62)
            write_poly(overlay_b / "Sweden_Stockholm.poly", 17, 59, 19, 60)
            countries = {
                "id": "Countries",
                "v": 1,
                "g": [{"id": "Norway", "g": [{"id": "Norway_Hedmark", "s": 0}]}],
            }
            cpath = t / "overlay" / "countries.txt"
            cpath.write_text(json.dumps(countries))
            proc = subprocess.run(
                [
                    "python3",
                    str(VALIDATE),
                    "--leafset",
                    str(leafset),
                    "--borders-dir",
                    str(borders),
                    "--overlay-borders",
                    str(overlay_b),
                    "--overlay-countries",
                    str(cpath),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("leftover", proc.stderr)


if __name__ == "__main__":
    unittest.main()
