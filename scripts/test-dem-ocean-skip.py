#!/usr/bin/env python3
"""Unit tests for DEM ocean-skip via Osmosis .poly + 404 negative cache."""

from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from dem_ocean_404_cache import load_missing, remember_missing  # noqa: E402
from dem_poly_filter import (  # noqa: E402
    bbox_tiles,
    classify_bbox_tiles,
    parse_osmosis_poly,
    point_in_poly,
    tile_intersects_poly,
    try_load_poly,
)


SYNTHETIC_POLY = """synthetic_box
1
   0.0   0.0
   2.0   0.0
   2.0   2.0
   0.0   2.0
   0.0   0.0
END
END
"""


class PolyParseAndClassify(unittest.TestCase):
    def test_parse_and_point_in_poly(self):
        rings = parse_osmosis_poly(SYNTHETIC_POLY)
        self.assertEqual(len(rings), 1)
        self.assertTrue(point_in_poly(1.0, 1.0, rings))
        self.assertFalse(point_in_poly(3.0, 3.0, rings))
        self.assertFalse(point_in_poly(-1.0, 1.0, rings))

    def test_skips_only_non_intersecting_tiles(self):
        rings = parse_osmosis_poly(SYNTHETIC_POLY)
        # Land covers [0,2]x[0,2] in lon/lat → tiles (0,0),(0,1),(1,0),(1,1)
        tiles = bbox_tiles(-1.0, -1.0, 3.5, 3.5)
        fetch, skip = classify_bbox_tiles(tiles, rings, samples=9)
        fetch_set = set(fetch)
        # Interior land tiles must be fetched.
        for t in ((0, 0), (0, 1), (1, 0), (1, 1)):
            self.assertIn(t, fetch_set, msg=f"land tile {t} must fetch")
        # Far ocean corner must skip (no shared edge with the [0,2]x[0,2] box).
        self.assertIn((3, 3), skip)
        self.assertNotIn((3, 3), fetch_set)
        # Tiles that only touch a corner/edge of the poly are fetched (conservative).
        self.assertIn((-1, -1), fetch_set)

    def test_try_load_fail_open(self):
        self.assertIsNone(try_load_poly("/nonexistent/nope.poly"))
        with tempfile.NamedTemporaryFile(suffix=".poly", mode="w", encoding="utf-8") as tf:
            tf.write("not a poly\n")
            tf.flush()
            self.assertIsNone(try_load_poly(tf.name))


class NegCache(unittest.TestCase):
    def test_remember_and_load(self):
        with tempfile.TemporaryDirectory() as td:
            elev = Path(td)
            self.assertEqual(load_missing(elev), set())
            n = remember_missing(elev, ["S19W032", "S19W031", "S19W032"])
            self.assertEqual(n, 2)
            self.assertEqual(load_missing(elev), {"S19W032", "S19W031"})
            n2 = remember_missing(elev, ["S19W031"])
            self.assertEqual(n2, 0)


class NordestePolySmoke(unittest.TestCase):
    POLY = Path("/media/navi/navi-server/data/scratch/dem-ocean-skip-test/nordeste.poly")

    @unittest.skipUnless(
        Path("/media/navi/navi-server/data/scratch/dem-ocean-skip-test/nordeste.poly").is_file(),
        "nordeste.poly not in isolated test dir",
    )
    def test_nordeste_skips_some_ocean(self):
        rings = try_load_poly(self.POLY)
        self.assertIsNotNone(rings)
        tiles = bbox_tiles(-18.59514, -48.77554, 2.673015, -28.42028)
        fetch, skip = classify_bbox_tiles(tiles, rings)
        self.assertEqual(len(fetch) + len(skip), len(tiles))
        self.assertGreater(len(skip), 0)
        self.assertGreater(len(fetch), 0)
        # Deep Atlantic corner of AABB should skip.
        self.assertFalse(tile_intersects_poly(-19, -29, rings, samples=9))


if __name__ == "__main__":
    unittest.main()
