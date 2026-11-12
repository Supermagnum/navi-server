#!/usr/bin/env python3
"""Mapterhorn tile 404 / soft-miss must not abort elevation sampling."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
MOD_PATH = ROOT / "scripts/mwm/build-elevation-overrides.py"


def _load():
    spec = importlib.util.spec_from_file_location("build_elevation_overrides", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class ElevationTile404Tests(unittest.TestCase):
    def test_http_404_is_soft_miss_and_memoized(self) -> None:
        m = _load()
        with tempfile.TemporaryDirectory() as td:
            cache = m.TileCache(Path(td), max_bytes=10 * 1024**2, zoom=12, min_zoom=10)

            def fake_urlopen(req, timeout=15):  # noqa: ARG001
                raise urllib.error.HTTPError(
                    getattr(req, "full_url", "https://tiles.mapterhorn.com/x"),
                    404,
                    "Not Found",
                    hdrs=None,
                    fp=None,
                )

            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                img, z, x, y = cache.get_image(60.0, 10.0)
                self.assertIsNone(img)
                self.assertGreaterEqual(cache.not_found, 1)
                misses = cache.not_found
                fetches = cache.fetches
                img2, _, _, _ = cache.get_image(60.0, 10.0)
                self.assertIsNone(img2)
                # Second call must not re-hit the network (memoized + .404 markers).
                self.assertEqual(cache.not_found, misses)
                self.assertEqual(cache.fetches, fetches)
                # Markers written for attempted zooms.
                markers = list(Path(td).rglob("*.404"))
                self.assertGreaterEqual(len(markers), 1)

    def test_sample_returns_none_on_total_miss(self) -> None:
        m = _load()
        with tempfile.TemporaryDirectory() as td:
            cache = m.TileCache(Path(td), max_bytes=10 * 1024**2, zoom=12, min_zoom=10)

            def fake_urlopen(req, timeout=15):  # noqa: ARG001
                raise urllib.error.HTTPError(
                    "https://tiles.mapterhorn.com/x",
                    404,
                    "Not Found",
                    hdrs=None,
                    fp=None,
                )

            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                self.assertIsNone(cache.sample(60.0, 10.0))


if __name__ == "__main__":
    unittest.main()
