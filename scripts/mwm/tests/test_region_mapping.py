"""Schema + overlap unit tests for region↔leaf mapping (synthetic polys)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from shapely.geometry import box

import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "mwm" / "lib"))
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

from region_mapping import (  # noqa: E402
    OverlapConfig,
    THRESHOLD_JUSTIFICATION,
    annotate_shared_leaves,
    apply_display_overrides,
    diff_migrations,
    enrich_country_groups_sizes,
    humanize_region_id,
    iter_country_group_leaves,
    overlap_region_to_leaves,
    parse_countries_hierarchy,
    review_flags,
    to_publish_region_map,
)


def _write_poly(path: Path, name: str, coords: list[tuple[float, float]]) -> None:
    lines = [name, "1"]
    for lon, lat in coords:
        lines.append(f"\t{lon}\t{lat}")
    lines.append("END")
    lines.append("END")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestRegionMappingCore(unittest.TestCase):
    def test_overlap_threshold_and_micro(self):
        # Region 0..10 x 0..10. Leaf A covers 60%. Leaf B covers 0.2% (micro).
        region = box(0, 0, 10, 10)
        leaf_a = box(0, 0, 6, 10)
        leaf_b = box(9.9, 0, 10.1, 10)  # thin strip ~2% actually — shrink
        leaf_b = box(9.98, 0, 10.02, 10)  # ~0.4% of region
        leaf_c = box(9.995, 0, 10.005, 10)  # ~0.1%

        class Idx:
            names = ["LeafA", "LeafB", "LeafC"]
            geoms = [leaf_a, leaf_b, leaf_c]
            areas = [leaf_a.area, leaf_b.area, leaf_c.area]

            class T:
                def query(self, g):
                    return [0, 1, 2]

            tree = T()

        cfg = OverlapConfig(include_threshold_pct=0.5, micro_threshold_pct=0.05)
        result = overlap_region_to_leaves(region, Idx(), cfg)
        leaves = {x["leaf"]: x for x in result["comaps_leaves"]}
        self.assertIn("LeafA", leaves)
        self.assertGreaterEqual(leaves["LeafA"]["coverage_of_region_pct"], 50.0)
        self.assertEqual(leaves["LeafA"]["include_reason"], "threshold")
        # LeafB/LeafC are sub-0.5% land-border strips → micro_land_overlap
        self.assertIn("LeafB", leaves)
        self.assertIn("LeafC", leaves)
        self.assertEqual(leaves["LeafB"]["include_reason"], "micro_land_overlap")
        self.assertEqual(leaves["LeafC"]["include_reason"], "micro_land_overlap")
        self.assertGreater(result["union_coverage_pct"], 50.0)
        self.assertLess(result["uncovered_pct"], 50.0)

    def test_shared_and_review(self):
        regions = {
            "r1": {
                "union_coverage_pct": 90.0,
                "uncovered_pct": 10.0,
                "comaps_leaves": [{"leaf": "Shared", "coverage_of_region_pct": 90}],
            },
            "r2": {
                "union_coverage_pct": 80.0,
                "uncovered_pct": 20.0,
                "comaps_leaves": [
                    {"leaf": "Shared", "coverage_of_region_pct": 50},
                    {"leaf": "Only2", "coverage_of_region_pct": 30},
                ],
            },
            "r3": {
                "union_coverage_pct": 0.0,
                "uncovered_pct": 100.0,
                "comaps_leaves": [],
            },
        }
        shared = annotate_shared_leaves(regions)
        self.assertEqual(shared["Shared"], ["r1", "r2"])
        self.assertEqual(
            regions["r1"]["comaps_leaves"][0]["shared_with_navi_regions"], ["r2"]
        )
        review = review_flags(regions, OverlapConfig(gap_warn_pct=0.5, max_leaves_warn=1))
        self.assertIn("r3", review["unmapped"])
        self.assertTrue(any(g["bake_id"] == "r1" for g in review["gaps"]))
        self.assertTrue(any(t["bake_id"] == "r2" for t in review["too_many_leaves"]))

    def test_countries_hierarchy_and_overrides(self):
        countries = {
            "id": "Countries",
            "v": 261105,
            "g": [
                {"id": "World", "s": 1},
                {
                    "id": "Norway",
                    "g": [
                        {
                            "id": "Norway_Hedmark",
                            "s": 100,
                            "sha1_base64": "abc",
                        },
                        {
                            "id": "Norway_Oppland",
                            "s": 200,
                            "sha1_base64": "def",
                        },
                    ],
                },
                {
                    "id": "Vietnam",
                    "g": [
                        {"id": "Vietnam_North", "s": 10, "sha1_base64": "n"},
                        {"id": "Vietnam_South", "s": 20, "sha1_base64": "s"},
                    ],
                },
                {
                    "id": "United States of America",
                    "g": [
                        {
                            "id": "Alabama",
                            "g": [
                                {
                                    "id": "US_Alabama_Birmingham",
                                    "s": 5,
                                    "sha1_base64": "b",
                                }
                            ],
                        },
                        {"id": "US_Alaska", "s": 9, "sha1_base64": "a"},
                    ],
                },
            ],
        }
        groups = parse_countries_hierarchy(countries)
        by_id = {g["id"]: g for g in groups}
        self.assertIn("Norway", by_id)
        self.assertEqual(len(by_id["Norway"]["leaves"]), 2)
        self.assertIn("Vietnam", by_id)
        self.assertEqual(
            [x["id"] for x in by_id["Vietnam"]["leaves"]],
            ["Vietnam_North", "Vietnam_South"],
        )
        self.assertIn("United States of America", by_id)
        usa = by_id["United States of America"]
        self.assertEqual([x["id"] for x in usa["leaves"]], ["US_Alaska"])
        self.assertEqual(usa["subgroups"][0]["id"], "Alabama")
        self.assertEqual(
            usa["subgroups"][0]["parent_groups"], ["United States of America"]
        )

        sizes = {
            "Norway_Hedmark": {"mwm_bytes": 100, "sidecar_bytes": 10},
            "Norway_Oppland": {"mwm_bytes": 200, "sidecar_bytes": 20},
        }
        groups = enrich_country_groups_sizes(groups, sizes)
        norway = next(g for g in groups if g["id"] == "Norway")
        self.assertEqual(norway["total_mwm_bytes"], 300)
        self.assertEqual(norway["total_sidecar_bytes"], 30)

        overrides = {
            "leaves": {
                "Norway_Hedmark": {
                    "display_name": "Innlandet (Hedmark)",
                    "note": "2020 reform",
                }
            }
        }
        apply_display_overrides(groups, overrides)
        hed = next(x for x in norway["leaves"] if x["id"] == "Norway_Hedmark")
        self.assertEqual(hed["display_name"], "Innlandet (Hedmark)")
        self.assertTrue(hed["display_name_overridden"])

        leaves = list(iter_country_group_leaves(groups))
        self.assertEqual(len(leaves), len(set(leaves)))
        self.assertIn("US_Alabama_Birmingham", leaves)
        self.assertNotIn("World", leaves)

    def test_migration_schema_and_diff(self):
        migration = {
            "schema": "navi-mwm-region-migration/v1",
            "comaps_release": "261105",
            "include_threshold_pct": 0.5,
            "micro_threshold_pct": 0.05,
            "threshold_justification": THRESHOLD_JUSTIFICATION,
            "regions": {
                "europe_norway_ostlandet": {
                    "region_id": "europe/norway/ostlandet",
                    "display_name": "Østlandet",
                    "union_coverage_pct": 99.0,
                    "uncovered_pct": 1.0,
                    "comaps_leaves": [
                        {
                            "leaf": "Norway_Hedmark",
                            "coverage_of_region_pct": 28.0,
                            "overspill_pct": 0.0,
                            "leaf_mostly_outside_region": False,
                            "include_reason": "threshold",
                        },
                        {
                            "leaf": "Norway_Southern",
                            "coverage_of_region_pct": 25.0,
                            "overspill_pct": 47.0,
                            "leaf_mostly_outside_region": False,
                            "include_reason": "threshold",
                        },
                    ],
                }
            },
            "shared_leaves": {"Norway_Southern": ["europe_norway_ostlandet", "europe_norway_sorlandet"]},
            "country_groups": [
                {
                    "id": "Norway",
                    "leaves": [{"id": "Norway_Hedmark"}, {"id": "Norway_Southern"}],
                    "subgroups": [],
                }
            ],
        }
        pub = to_publish_region_map(migration, "test limitation")
        self.assertEqual(pub["schema"], "navi-mwm-region-leaf-mapping/v2")
        self.assertIn("europe_norway_ostlandet", pub["navi_region_to_comaps_leaves"])
        self.assertEqual(pub["shared_leaves"]["Norway_Southern"][0], "europe_norway_ostlandet")

        prev = {
            "regions": {
                "europe_norway_ostlandet": {
                    "comaps_leaves": [{"leaf": "Norway_Hedmark"}],
                }
            },
            "country_groups": [
                {"id": "Norway", "leaves": [{"id": "Norway_Hedmark"}], "subgroups": []}
            ],
        }
        diff = diff_migrations(prev, migration)
        self.assertEqual(diff["schema"], "navi-mwm-region-migration-diff/v1")
        self.assertIn("Norway_Southern", diff["leaves_added"])
        self.assertTrue(
            any(
                c["bake_id"] == "europe_norway_ostlandet" and "Norway_Southern" in c["added"]
                for c in diff["mapping_changes"]
            )
        )

    def test_humanize(self):
        self.assertEqual(humanize_region_id("europe/norway/ostlandet"), "Ostlandet")

    def test_committed_overrides_schema(self):
        path = ROOT / "config" / "mwm" / "catalog" / "display_name_overrides.json"
        self.assertTrue(path.is_file())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "navi-mwm-display-name-overrides/v1")
        self.assertIn("Norway_Hedmark", data["leaves"])
        self.assertIn("Norway_Oppland", data["leaves"])
        self.assertIsInstance(data["likely_outdated_elsewhere"], list)
        self.assertGreaterEqual(len(data["likely_outdated_elsewhere"]), 1)


class TestPolyRoundTrip(unittest.TestCase):
    def test_load_poly_and_index(self):
        from region_mapping import build_leaf_index, load_poly_file

        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            borders = td_path / "borders"
            borders.mkdir()
            # Square leaf
            _write_poly(
                borders / "Test_Leaf.poly",
                "Test_Leaf",
                [(0, 0), (5, 0), (5, 5), (0, 5), (0, 0)],
            )
            idx = build_leaf_index(borders, simplify_deg=0)
            self.assertEqual(idx.names, ["Test_Leaf"])
            region_poly = td_path / "region.poly"
            _write_poly(
                region_poly,
                "region",
                [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
            )
            geom = load_poly_file(region_poly, simplify_deg=0)
            result = overlap_region_to_leaves(geom, idx, OverlapConfig())
            self.assertEqual(len(result["comaps_leaves"]), 1)
            self.assertEqual(result["comaps_leaves"][0]["leaf"], "Test_Leaf")
            self.assertGreater(result["comaps_leaves"][0]["coverage_of_region_pct"], 20)


if __name__ == "__main__":
    unittest.main()
