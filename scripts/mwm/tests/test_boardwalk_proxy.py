#!/usr/bin/env python3
"""Boardwalk proxy: foot/path/track bridge ∩ wetland → carve-out."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "annotate-wetland-from-mwm.py"


class TestBoardwalkProxy(unittest.TestCase):
    def _run(self, roads, wetlands):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "r.jsonl").write_text("\n".join(json.dumps(r) for r in roads) + "\n")
            (td / "w.jsonl").write_text("\n".join(json.dumps(w) for w in wetlands) + "\n")
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
            wet = [json.loads(l) for l in out_w.read_text().splitlines() if l.strip()]
            bw = [json.loads(l) for l in out_b.read_text().splitlines() if l.strip()]
            return stats, wet, bw

    def test_bridge_touching_wetland_is_boardwalk_not_penalized(self):
        wetlands = [
            {
                "feature_id": 1,
                "class": 2,
                "subtype": "swamp",
                "triangles": [[[61.0, 11.0], [61.0, 11.2], [61.2, 11.1]]],
            }
        ]
        roads = [
            {
                "feature_id": 99,
                "points": [
                    {"lat": 61.05, "lon": 11.05, "alt_m": 0},
                    {"lat": 61.08, "lon": 11.08, "alt_m": 0},
                ],
                "types": ["highway-footway-bridge"],
            }
        ]
        stats, wet, bw = self._run(roads, wetlands)
        self.assertEqual(stats["wetland_records"], 0)
        self.assertEqual(stats["boardwalk_segments"], 1)
        self.assertEqual(len(bw), 2)  # both directions
        self.assertTrue(all(r["flag"] == 1 for r in bw))

    def test_bridge_without_wetland_not_boardwalk(self):
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
                "feature_id": 5,
                "points": [
                    {"lat": 61.05, "lon": 11.05, "alt_m": 0},
                    {"lat": 61.08, "lon": 11.08, "alt_m": 0},
                ],
                "types": ["highway-path-bridge"],
            }
        ]
        stats, wet, bw = self._run(roads, wetlands)
        self.assertEqual(stats["boardwalk_records"], 0)
        self.assertEqual(bw, [])
        self.assertEqual(wet, [])

    def test_plain_track_still_gets_soft(self):
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
                "feature_id": 3,
                "points": [
                    {"lat": 61.05, "lon": 11.05, "alt_m": 0},
                    {"lat": 61.08, "lon": 11.08, "alt_m": 0},
                ],
                "types": ["highway-track"],
            }
        ]
        stats, wet, bw = self._run(roads, wetlands)
        self.assertEqual(stats["soft_records"], 2)
        self.assertEqual(stats["boardwalk_records"], 0)


if __name__ == "__main__":
    unittest.main()
