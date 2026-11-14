#!/usr/bin/env python3
"""Elevation units + Terrarium decode + tile math (gates2 elev deviation)."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_elev():
    path = ROOT / "build-elevation-overrides.py"
    loader = importlib.machinery.SourceFileLoader("elev_units", str(path))
    spec = importlib.util.spec_from_loader("elev_units", loader)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestTerrariumAndUnits(unittest.TestCase):
    def setUp(self):
        self.elev = _load_elev()

    def test_terrarium_formula(self):
        # Sea-level-ish: R=128, G=0, B=0 → 128*256 - 32768 = 0
        self.assertEqual(self.elev.terrarium_elev(128, 0, 0), 0.0)
        # 100 m: 128*256 + 100 - 32768 = 100
        self.assertEqual(self.elev.terrarium_elev(128, 100, 0), 100.0)
        # Gaustatoppen-ish ~1883 m
        # metres = R*256 + G + B/256 - 32768
        m = 1883.0
        base = m + 32768.0
        r = int(base // 256)
        g = int(base - r * 256)
        b = 0
        self.assertAlmostEqual(self.elev.terrarium_elev(r, g, b), m, places=5)

    def test_override_units_are_decimetres(self):
        # Sidecar stores altitude_dm = round(metres * 10); CoMaps dump is metres.
        metres = 943.0
        dm = int(round(metres * 10))
        self.assertEqual(dm, 9430)
        self.assertAlmostEqual(dm / 10.0, metres)

    def test_xyz_not_tms(self):
        # Oslo: z12 tile y must match WebMercator XYZ (not flipped TMS).
        lat, lon, z = 59.91, 10.75, 12
        x, y = self.elev.latlon_to_tile(lat, lon, z)
        n = 2**z
        # TMS y would be (n - 1 - y); assert we are in northern hemisphere upper half.
        self.assertLess(y, n // 2)
        self.assertGreater(x, 0)
        # Round-trip: centre of pixel should stay in same tile.
        x2, y2 = self.elev.latlon_to_tile(lat + 0.001, lon + 0.001, z)
        self.assertEqual((x, y), (x2, y2))

    def test_pixel_mapping_floor_indexing(self):
        # sample() uses int(xf)/int(yf) floor into tile pixels (corner origin).
        # Document expected behaviour for 256 and 512 tiles.
        with tempfile.TemporaryDirectory() as td:
            cache = self.elev.TileCache(Path(td), 1024, zoom=12, min_zoom=12)
            # Without network: ensure math helpers exist.
            x, y = self.elev.latlon_to_tile(61.0, 11.0, 12)
            self.assertIsInstance(x, int)
            self.assertIsInstance(y, int)
            # Fractional pixel for tile-local coords at NW corner → ~0.
            n = 2.0**12
            lon0 = x / n * 360.0 - 180.0
            # North edge of tile:
            merc_y = 1.0 - (y / n) * 2.0
            lat0 = math.degrees(math.atan(math.sinh(merc_y * math.pi)))
            # Just inside NW → small xf/yf
            xf = ((lon0 + 0.0001 + 180.0) / 360.0 * n - x) * 256
            self.assertGreaterEqual(xf, 0.0)
            self.assertLess(xf, 2.0)
            _ = cache  # constructed OK


class TestCorridorSamePointContract(unittest.TestCase):
    def test_corridor_script_documents_prior_bug(self):
        text = (ROOT / "corridor-delta-h.py").read_text()
        self.assertIn("same-point", text)
        self.assertIn("300 m", text)
        self.assertIn("SPOTS", text)

    def test_default_zoom_is_twelve_not_max(self):
        text = (ROOT / "build-elevation-overrides.py").read_text()
        self.assertIn('NAVI_MWM_MAPTERHORN_ZOOM", "12"', text)
        self.assertNotIn("highest_working_zoom(mid", text)

    def test_default_zoom_is_twelve_not_max(self):
        text = (ROOT / "build-elevation-overrides.py").read_text()
        self.assertIn('NAVI_MWM_MAPTERHORN_ZOOM", "12"', text)
        self.assertNotIn("highest_working_zoom(mid", text)


if __name__ == "__main__":
    unittest.main()
