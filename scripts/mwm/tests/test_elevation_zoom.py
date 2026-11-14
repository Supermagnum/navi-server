#!/usr/bin/env python3
"""Tests for Mapterhorn zoom selection / fallback helpers."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_elev():
    path = ROOT / "build-elevation-overrides.py"
    spec = importlib.util.spec_from_file_location("elev", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestElevationZoom(unittest.TestCase):
    def test_latlon_to_tile_stable(self):
        elev = _load_elev()
        x, y = elev.latlon_to_tile(61.05, 11.47, 12)
        self.assertIsInstance(x, int)
        self.assertIsInstance(y, int)
        x2, y2 = elev.latlon_to_tile(61.05, 11.47, 15)
        # Higher zoom → larger tile indices
        self.assertGreaterEqual(x2, x)
        self.assertGreaterEqual(y2, y)

    def test_tile_cache_falls_back_on_404(self):
        elev = _load_elev()
        with mock.patch.object(elev.TileCache, "_fetch_one") as fetch:
            # z15 miss, z14 hit
            img = object()

            def side(z, x, y):
                if z >= 15:
                    return None
                return img

            fetch.side_effect = side
            cache = elev.TileCache(Path("/tmp/navi-elev-zoom-test-cache"), 1 << 20, zoom=15, min_zoom=12)
            got, z, _x, _y = cache.get_image(61.05, 11.47)
            self.assertIs(got, img)
            self.assertEqual(z, 14)
            self.assertGreaterEqual(cache.fallback_ok, 1)


if __name__ == "__main__":
    unittest.main()
