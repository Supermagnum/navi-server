#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

spec = importlib.util.spec_from_file_location(
    "annotate_vehicle_limits", ROOT / "annotate-vehicle-limits.py"
)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class AnnotateVehicleLimitsTest(unittest.TestCase):
    def test_segment_both_ends_on_way(self) -> None:
        way = {
            "osm_way_id": 31776674,
            "tags": {"highway": "secondary", "name": "Fokholgutua", "maxheight": "2.7"},
            "nodes": [(60.7300, 11.1863), (60.7301, 11.1864), (60.7302, 11.1865)],
            "name": "fokholgutua",
            "highway": "secondary",
        }
        # Road whose middle segment lies on the way; first segment has an off-way end.
        roads = [
            {
                "feature_id": 100,
                "points": [
                    (60.7299, 11.1860),  # off way
                    (60.7300, 11.1863),
                    (60.7301, 11.1864),
                ],
                "highway": "secondary",
                "name": "fokholgutua",
                "types": ["highway-secondary"],
            }
        ]
        rows = mod.match_way_to_roads(way, roads)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["segment_index"], 1)
        self.assertEqual(rows[0]["max_height_cm"], 270)

    def test_name_mismatch_unmatched(self) -> None:
        way = {
            "osm_way_id": 1,
            "tags": {"highway": "secondary", "name": "A", "maxheight": "3.0"},
            "nodes": [(60.0, 11.0), (60.001, 11.001)],
            "name": "a",
            "highway": "secondary",
        }
        roads = [
            {
                "feature_id": 1,
                "points": [(60.0, 11.0), (60.001, 11.001)],
                "highway": "secondary",
                "name": "b",
                "types": ["highway-secondary"],
            }
        ]
        self.assertEqual(mod.match_way_to_roads(way, roads), [])

    def test_default_height_skipped(self) -> None:
        way = {
            "osm_way_id": 2,
            "tags": {"highway": "secondary", "maxheight": "default"},
            "nodes": [(60.0, 11.0), (60.001, 11.001)],
            "name": "",
            "highway": "secondary",
        }
        roads = [
            {
                "feature_id": 9,
                "points": [(60.0, 11.0), (60.001, 11.001)],
                "highway": "secondary",
                "name": "",
                "types": ["highway-secondary"],
            }
        ]
        self.assertEqual(mod.match_way_to_roads(way, roads), [])

    def test_annotate_report_lists_unmatched(self) -> None:
        ways = [
            {
                "osm_way_id": 99,
                "tags": {"highway": "primary", "name": "Ghost", "maxweight": "7.5"},
                "nodes": [(61.0, 10.0), (61.001, 10.001)],
                "name": "ghost",
                "highway": "primary",
            }
        ]
        records, report = mod.annotate(ways, roads=[])
        self.assertEqual(records, [])
        self.assertEqual(report["unmatched_ways"], 1)
        self.assertEqual(report["unmatched"][0]["osm_way_id"], 99)


if __name__ == "__main__":
    unittest.main()
