#!/usr/bin/env python3
"""Unit tests for scripts/lib/dem_coverage.py (no network, no live elev tree)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_LIB = Path(__file__).resolve().parent / "lib"
sys.path.insert(0, str(SCRIPT_LIB))

from dem_coverage import (  # noqa: E402
    bbox_cells,
    cell_has_dem,
    coverage_for_bbox,
    elev_has_any_dem,
    region_dem_ok,
    tile_stem,
)


class DemCoverageTests(unittest.TestCase):
    def test_tile_stem(self):
        self.assertEqual(tile_stem(59, 5), "N59E005")
        self.assertEqual(tile_stem(-12, -8), "S12W008")

    def test_bbox_cells_count(self):
        cells = bbox_cells(59.1, 5.2, 60.9, 6.8)
        self.assertEqual(len(cells), 4)  # lat 59..60, lon 5..6

    def test_region_dem_ok_with_bbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            elev = Path(tmp) / "elev"
            stem = tile_stem(59, 5)
            dest = elev / "copernicus" / stem
            dest.mkdir(parents=True)
            (dest / "Copernicus_DSM_COG_10_N59_00_E005_00_DEM.tif").write_bytes(
                b"fake-dem"
            )
            bboxes = Path(tmp) / "regions.conf.bboxes.json"
            bboxes.write_text(
                json.dumps({"europe_norway_sorlandet": [59.0, 5.0, 59.5, 5.5]}) + "\n",
                encoding="utf-8",
            )
            ok, msg = region_dem_ok(elev, "europe_norway_sorlandet", bboxes)
            self.assertTrue(ok, msg)
            self.assertIn("present=1/", msg)

            ok2, msg2 = region_dem_ok(elev, "europe_norway_trondelag", bboxes)
            # no bbox entry → any-dem fallback
            self.assertTrue(ok2, msg2)

            empty = Path(tmp) / "empty"
            empty.mkdir()
            bboxes2 = Path(tmp) / "b2.json"
            bboxes2.write_text(
                json.dumps({"europe_norway_trondelag": [62.0, 8.0, 63.0, 9.0]}) + "\n",
                encoding="utf-8",
            )
            ok3, msg3 = region_dem_ok(empty, "europe_norway_trondelag", bboxes2)
            self.assertFalse(ok3, msg3)
            self.assertIn("0/", msg3)

    def test_elev_has_any_dem_ignores_lock_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            elev = Path(tmp)
            (elev / ".tile_locks").mkdir()
            (elev / ".tile_locks" / "N59E005.lock").write_text("")
            (elev / "copernicus").mkdir()
            (elev / "copernicus" / ".gitkeep").write_text("")
            self.assertFalse(elev_has_any_dem(elev))
            cell = elev / "copernicus" / "N59E005"
            cell.mkdir()
            (cell / "x.tif").write_bytes(b"x")
            self.assertTrue(cell_has_dem(elev, 59, 5))
            present, total = coverage_for_bbox(elev, 59.0, 5.0, 59.1, 5.1)
            self.assertEqual((present, total), (1, 1))


if __name__ == "__main__":
    unittest.main()
