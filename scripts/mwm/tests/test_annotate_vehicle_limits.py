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
    def test_segment_both_ends_within_tolerance(self) -> None:
        way = {
            "osm_way_id": 31776674,
            "tags": {"highway": "secondary", "name": "Fokholgutua", "maxheight": "2.7"},
            "nodes": [(60.7300, 11.1863), (60.7301, 11.1864), (60.7302, 11.1865)],
            "name": "fokholgutua",
            "highway": "secondary",
            "is_bridge": False,
            "is_tunnel": False,
        }
        # Slightly offset MWM geometry (~1–2 m); middle segment aligns with way.
        roads = [
            {
                "feature_id": 100,
                "points": [
                    (60.7299, 11.1860),  # far end → reject this segment
                    (60.7300, 11.1863),
                    (60.7301, 11.1864),
                ],
                "highway": "secondary",
                "is_bridge": False,
                "is_tunnel": False,
                "name": "fokholgutua",
                "types": ["highway-secondary"],
            }
        ]
        rows = mod.match_way_to_roads(way, roads)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["segment_index"], 1)
        self.assertEqual(rows[0]["max_height_cm"], 270)

    def test_bridge_class_suffix_matches(self) -> None:
        way = {
            "osm_way_id": 34106197,
            "tags": {
                "highway": "secondary",
                "name": "Stai bru",
                "bridge": "yes",
                "maxheight": "3.7",
            },
            "nodes": [(61.8100, 10.9500), (61.8101, 10.9502)],
            "name": "stai bru",
            "highway": "secondary",
            "is_bridge": True,
            "is_tunnel": False,
        }
        roads = [
            {
                "feature_id": 7109,
                "points": [(61.8100, 10.9500), (61.8101, 10.9502)],
                "highway": "secondary",
                "is_bridge": True,
                "is_tunnel": False,
                "name": "",
                "types": ["highway-secondary-bridge"],
            }
        ]
        rows = mod.match_way_to_roads(way, roads)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["feature_id"], 7109)

    def test_road_under_bridge_rejected(self) -> None:
        way = {
            "osm_way_id": 1,
            "tags": {"highway": "secondary", "bridge": "yes", "maxheight": "3.0"},
            "nodes": [(60.0, 11.0), (60.0001, 11.0001)],
            "name": "",
            "highway": "secondary",
            "is_bridge": True,
            "is_tunnel": False,
        }
        # Same geometry but MWM feature is NOT a bridge (road underneath).
        roads = [
            {
                "feature_id": 2,
                "points": [(60.0, 11.0), (60.0001, 11.0001)],
                "highway": "secondary",
                "is_bridge": False,
                "is_tunnel": False,
                "name": "",
                "types": ["highway-secondary"],
            }
        ]
        self.assertEqual(mod.match_way_to_roads(way, roads), [])

    def test_name_mismatch_still_matches(self) -> None:
        """Name is a tie-breaker, not a hard requirement."""
        way = {
            "osm_way_id": 1,
            "tags": {"highway": "secondary", "name": "A", "maxheight": "3.0"},
            "nodes": [(60.0, 11.0), (60.001, 11.001)],
            "name": "a",
            "highway": "secondary",
            "is_bridge": False,
            "is_tunnel": False,
        }
        roads = [
            {
                "feature_id": 1,
                "points": [(60.0, 11.0), (60.001, 11.001)],
                "highway": "secondary",
                "is_bridge": False,
                "is_tunnel": False,
                "name": "b",
                "types": ["highway-secondary"],
            }
        ]
        rows = mod.match_way_to_roads(way, roads)
        self.assertEqual(len(rows), 1)

    def test_default_height_skipped(self) -> None:
        way = {
            "osm_way_id": 2,
            "tags": {"highway": "secondary", "maxheight": "default"},
            "nodes": [(60.0, 11.0), (60.001, 11.001)],
            "name": "",
            "highway": "secondary",
            "is_bridge": False,
            "is_tunnel": False,
        }
        roads = [
            {
                "feature_id": 9,
                "points": [(60.0, 11.0), (60.001, 11.001)],
                "highway": "secondary",
                "is_bridge": False,
                "is_tunnel": False,
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
                "is_bridge": False,
                "is_tunnel": False,
            }
        ]
        records, report = mod.annotate(ways, roads=[])
        self.assertEqual(records, [])
        self.assertEqual(report["unmatched_ways"], 1)
        self.assertEqual(report["unmatched"][0]["osm_way_id"], 99)
        self.assertIn("reason", report["unmatched"][0])

    def test_parse_mwm_highway_bridge_suffix(self) -> None:
        base, is_br, is_tu = mod._parse_mwm_highway(["highway-primary-bridge"])
        self.assertEqual(base, "primary")
        self.assertTrue(is_br)
        self.assertFalse(is_tu)

    def test_junction_stub_short_side_feature_rejected(self) -> None:
        """Perpendicular short stub on a side feature must not inherit limits."""
        # East-west tagged way.
        way = {
            "osm_way_id": 10,
            "tags": {"highway": "secondary", "name": "Main", "maxheight": "3.5"},
            "nodes": [(60.0, 11.0), (60.0, 11.001)],
            "name": "main",
            "highway": "secondary",
            "is_bridge": False,
            "is_tunnel": False,
        }
        roads = [
            # Primary: follows the way.
            {
                "feature_id": 1,
                "points": [(60.0, 11.0), (60.0, 11.001)],
                "highway": "secondary",
                "is_bridge": False,
                "is_tunnel": False,
                "name": "main",
                "types": ["highway-secondary"],
            },
            # Side junction stub: both ends near the way but near-zero length
            # (quantised duplicate) — bearing skipped, different feature.
            {
                "feature_id": 2,
                "points": [(60.0, 11.0005), (60.0, 11.0005)],
                "highway": "secondary",
                "is_bridge": False,
                "is_tunnel": False,
                "name": "",
                "types": ["highway-secondary"],
            },
        ]
        # Need 2 distinct points for load; use tiny offset still < MIN_BEARING.
        roads[1]["points"] = [(60.000001, 11.0005), (60.000002, 11.0005)]
        guards: dict[str, int] = {}
        rows = mod.match_way_to_roads(way, roads, guard_counts=guards)
        fids = {r["feature_id"] for r in rows}
        self.assertIn(1, fids)
        self.assertNotIn(2, fids)
        self.assertGreaterEqual(guards.get("bearing_skipped_short_seg", 0), 1)
        self.assertGreaterEqual(guards.get("junction_stub_reject", 0), 1)

    def test_same_layer_bridge_skips_bearing_when_overlap_ok(self) -> None:
        """Bridge OSM + bridge MWM, ends in tol + overlap → no bearing reject."""
        way = {
            "osm_way_id": 42,
            "tags": {
                "highway": "unclassified",
                "bridge": "yes",
                "maxweight": "40",
            },
            "nodes": [(60.0, 11.0), (60.0, 11.00005)],
            "name": "",
            "highway": "unclassified",
            "is_bridge": True,
            "is_tunnel": False,
        }
        # Angled chord would fail a strict bearing check vs E-W OSM.
        roads = [
            {
                "feature_id": 7,
                "points": [(60.0, 11.0), (60.000035, 11.000035)],
                "highway": "unclassified",
                "is_bridge": True,
                "is_tunnel": False,
                "name": "",
                "types": ["highway-unclassified-bridge"],
            }
        ]
        guards: dict[str, int] = {}
        rows = mod.match_way_to_roads(way, roads, guard_counts=guards)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["feature_id"], 7)
        self.assertGreaterEqual(guards.get("bearing_skipped_same_layer", 0), 1)
        self.assertEqual(guards.get("bearing_reject", 0), 0)

    def test_overlap_rejects_short_perpendicular_when_bearing_skipped(self) -> None:
        """Short N-S stub: overlap runs first; along-way span ~0 → reject.

        Segment is below MIN_BEARING_SEGMENT_M, so bearing would be skipped;
        overlap-first short-circuits before bearing_skipped_short_seg.
        """
        way = {
            "osm_way_id": 11,
            "tags": {"highway": "secondary", "maxheight": "3.0"},
            "nodes": [(60.0, 11.0), (60.0, 11.002)],
            "name": "",
            "highway": "secondary",
            "is_bridge": False,
            "is_tunnel": False,
        }
        # ~1.1 m north-south at the way midpoint (within 8 m, below bearing min).
        roads = [
            {
                "feature_id": 9,
                "points": [(59.999995, 11.001), (60.000005, 11.001)],
                "highway": "secondary",
                "is_bridge": False,
                "is_tunnel": False,
                "name": "",
                "types": ["highway-secondary"],
            }
        ]
        guards: dict[str, int] = {}
        rows = mod.match_way_to_roads(way, roads, guard_counts=guards)
        self.assertEqual(rows, [])
        self.assertGreaterEqual(guards.get("overlap_reject", 0), 1)
        self.assertEqual(guards.get("bearing_skipped_short_seg", 0), 0)
        self.assertEqual(guards.get("bearing_reject", 0), 0)

    def test_special_value_excluded_from_match_rate(self) -> None:
        """none/default/unsigned/no-limit out of denom; below_default recorded."""
        roads = [
            {
                "feature_id": 1,
                "points": [(60.0, 11.0), (60.001, 11.001)],
                "highway": "secondary",
                "is_bridge": False,
                "is_tunnel": False,
                "name": "",
                "types": ["highway-secondary"],
            }
        ]
        ways = [
            {
                "osm_way_id": 1,
                "tags": {"highway": "secondary", "maxheight": "3.0"},
                "nodes": [(60.0, 11.0), (60.001, 11.001)],
                "name": "",
                "highway": "secondary",
                "is_bridge": False,
                "is_tunnel": False,
            },
            {
                "osm_way_id": 2,
                "tags": {"highway": "secondary", "maxheight": "default"},
                "nodes": [(60.0, 11.0), (60.001, 11.001)],
                "name": "",
                "highway": "secondary",
                "is_bridge": False,
                "is_tunnel": False,
            },
            {
                "osm_way_id": 3,
                "tags": {"highway": "secondary", "maxheight": "none"},
                "nodes": [(60.0, 11.0), (60.001, 11.001)],
                "name": "",
                "highway": "secondary",
                "is_bridge": False,
                "is_tunnel": False,
            },
            {
                "osm_way_id": 4,
                "tags": {"highway": "secondary", "maxheight": "unsigned"},
                "nodes": [(60.0, 11.0), (60.001, 11.001)],
                "name": "",
                "highway": "secondary",
                "is_bridge": False,
                "is_tunnel": False,
            },
            {
                "osm_way_id": 5,
                "tags": {"highway": "secondary", "maxheight": "no-limit"},
                "nodes": [(60.0, 11.0), (60.001, 11.001)],
                "name": "",
                "highway": "secondary",
                "is_bridge": False,
                "is_tunnel": False,
            },
            {
                "osm_way_id": 6,
                "tags": {"highway": "secondary", "maxheight": "below_default"},
                "nodes": [(60.0, 11.0), (60.001, 11.001)],
                "name": "",
                "highway": "secondary",
                "is_bridge": False,
                "is_tunnel": False,
            },
            {
                "osm_way_id": 7,
                "tags": {"highway": "secondary", "maxheight": "4.0"},
                "nodes": [(61.0, 10.0), (61.001, 10.001)],
                "name": "ghost",
                "highway": "secondary",
                "is_bridge": False,
                "is_tunnel": False,
            },
        ]
        records, report = mod.annotate(ways, roads)
        self.assertEqual(report["non_numeric_no_limit"], 4)
        self.assertEqual(report["unknown_value"], 1)
        self.assertEqual(report["numeric_ways"], 2)
        self.assertEqual(report["matched_ways"], 1)
        self.assertEqual(report["unmatched_ways"], 1)
        self.assertAlmostEqual(report["match_rate"], 0.5)
        self.assertEqual(len(report["unknown_value_ways"]), 1)
        self.assertEqual(report["unknown_value_ways"][0]["osm_way_id"], 6)
        # No vehicle_limits records for specials.
        ids = {r.get("osm_way_id") for r in records}
        self.assertNotIn(2, ids)
        self.assertNotIn(6, ids)

    def test_classify_way_limit_tags_priority(self) -> None:
        self.assertEqual(
            mod.classify_way_limit_tags({"maxheight": "below_default", "maxweight": "default"}),
            "unknown_value",
        )
        self.assertEqual(
            mod.classify_way_limit_tags({"maxheight": "default", "maxweight": "7.5"}),
            "numeric",
        )
        self.assertEqual(
            mod.classify_way_limit_tags({"maxheight": "no-limit"}),
            "non_numeric_no_limit",
        )





if __name__ == "__main__":
    unittest.main()
