#!/usr/bin/env python3
"""Tests for MWM-native wetland binding (no PBF)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "annotate-wetland-from-mwm.py"
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))


class TestWetlandMwmBind(unittest.TestCase):
    def _run(self, roads, wetlands):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "r.jsonl").write_text(
                "\n".join(json.dumps(r) for r in roads) + "\n"
            )
            (td / "w.jsonl").write_text(
                "\n".join(json.dumps(w) for w in wetlands) + "\n"
            )
            out_w = td / "wet.jsonl"
            out_b = td / "bw.jsonl"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--roads-jsonl",
                    str(td / "r.jsonl"),
                    "--wetlands-jsonl",
                    str(td / "w.jsonl"),
                    "--out-wetland",
                    str(out_w),
                    "--out-boardwalk",
                    str(out_b),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            stats = json.loads(proc.stdout.strip().splitlines()[-1])
            recs = [
                json.loads(line)
                for line in out_w.read_text().splitlines()
                if line.strip()
            ]
            return stats, recs

    def test_segment_inside_soft(self):
        wetlands = [
            {
                "feature_id": 1,
                "class": 1,
                "subtype": "bog",
                "triangles": [[[61.0, 11.0], [61.0, 11.2], [61.2, 11.1]]],
            }
        ]
        roads = [
            {
                "feature_id": 42,
                "points": [
                    {"lat": 61.05, "lon": 11.05, "alt_m": 0},
                    {"lat": 61.08, "lon": 11.08, "alt_m": 0},
                ],
                "types": ["highway-track"],
            }
        ]
        stats, recs = self._run(roads, wetlands)
        self.assertEqual(stats["soft_records"], 2)  # both directions
        self.assertEqual(stats["hard_records"], 0)
        self.assertEqual({r["direction"] for r in recs}, {0, 1})
        self.assertTrue(all(r["class"] == 1 for r in recs))

    def test_hard_preferred_and_marsh_omitted(self):
        wetlands = [
            {
                "feature_id": 1,
                "class": 1,
                "subtype": "bog",
                "triangles": [[[61.0, 11.0], [61.0, 11.2], [61.2, 11.1]]],
            },
            {
                "feature_id": 2,
                "class": 2,
                "subtype": "swamp",
                "triangles": [[[61.0, 11.0], [61.0, 11.2], [61.2, 11.1]]],
            },
        ]
        roads = [
            {
                "feature_id": 7,
                "points": [
                    {"lat": 61.05, "lon": 11.05, "alt_m": 0},
                    {"lat": 61.08, "lon": 11.08, "alt_m": 0},
                ],
                "types": ["highway-path"],
            }
        ]
        stats, recs = self._run(roads, wetlands)
        self.assertEqual(stats["hard_records"], 2)
        self.assertEqual(stats["soft_records"], 0)
        self.assertTrue(all(r["class"] == 2 for r in recs))

    def test_miss_outside(self):
        wetlands = [
            {
                "feature_id": 1,
                "class": 1,
                "subtype": "bog",
                "triangles": [[[60.0, 10.0], [60.0, 10.1], [60.1, 10.05]]],
            }
        ]
        roads = [
            {
                "feature_id": 9,
                "points": [
                    {"lat": 61.05, "lon": 11.05, "alt_m": 0},
                    {"lat": 61.08, "lon": 11.08, "alt_m": 0},
                ],
                "types": ["highway-secondary"],
            }
        ]
        stats, recs = self._run(roads, wetlands)
        self.assertEqual(stats["wetland_records"], 0)
        self.assertEqual(recs, [])


class TestLeafScheduler(unittest.TestCase):
    def test_order_nordic_europe_world(self):
        from leaf_scheduler import leaf_sort_key, order_leaves, default_process_concurrency

        ordered = order_leaves(
            ["World", "Germany_Bavaria", "Norway_Hedmark", "France_Corsica", "Sweden_Stockholm"]
        )
        self.assertEqual(ordered[0], "Norway_Hedmark")
        self.assertEqual(ordered[1], "Sweden_Stockholm")
        self.assertLess(leaf_sort_key("Germany_Bavaria")[0], leaf_sort_key("World")[0])
        self.assertEqual(default_process_concurrency(available_gib=40, headroom_gib=16, per_leaf_gib=8.7), 2)
        self.assertEqual(default_process_concurrency(available_gib=20, headroom_gib=16, per_leaf_gib=8.7), 1)

    def test_resume_pending(self):
        from leaf_scheduler import pending_leaves

        pending = pending_leaves(
            ["A", "B", "C"],
            {"leaves": {"B": {}}},
            limit=0,
        )
        self.assertEqual(pending, ["A", "C"])


class TestSignatureVerify(unittest.TestCase):
    def test_valid_and_tampered(self):
        from nacl.signing import SigningKey
        from comaps_release import verify_countries_txt_sig

        sk = SigningKey.generate()
        vk = sk.verify_key
        msg = b'{"v":261105,"id":"Countries","g":[]}'
        sig = sk.sign(msg).signature
        pubkey_hex = bytes(vk).hex()
        self.assertTrue(verify_countries_txt_sig(msg, sig, pubkey_hex=pubkey_hex))
        tampered = msg.replace(b"261105", b"261106")
        self.assertFalse(verify_countries_txt_sig(tampered, sig, pubkey_hex=pubkey_hex))
        bad_sig = bytes(bytearray(sig)[0] ^ 0xFF) + sig[1:]
        self.assertFalse(verify_countries_txt_sig(msg, bad_sig, pubkey_hex=pubkey_hex))


class TestElevationZoomSelection(unittest.TestCase):
    def test_tile_math_and_fallback_order(self):
        sys.path.insert(0, str(ROOT))
        from importlib.util import spec_from_loader, module_from_spec
        import importlib.machinery

        path = ROOT / "build-elevation-overrides.py"
        loader = importlib.machinery.SourceFileLoader("elev", str(path))
        spec = spec_from_loader("elev", loader)
        assert spec and spec.loader
        mod = module_from_spec(spec)
        spec.loader.exec_module(mod)
        x, y = mod.latlon_to_tile(61.0, 11.0, 12)
        self.assertIsInstance(x, int)
        self.assertIsInstance(y, int)
        # Zoom fallback walks preferred → min_zoom
        cache = mod.TileCache(Path(tempfile.mkdtemp()), 1024, zoom=14, min_zoom=12)
        self.assertEqual(cache.zoom, 14)
        self.assertEqual(cache.min_zoom, 12)


if __name__ == "__main__":
    unittest.main()
