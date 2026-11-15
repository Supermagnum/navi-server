"""Spot-check committed 261105 region-migration + country_groups artifacts."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "config" / "mwm" / "catalog"
MIGRATION = CATALOG / "region-migration.json"
COUNTRY_GROUPS = CATALOG / "country_groups.json"
REGION_MAP = CATALOG / "region_leaf_mapping.json"


@unittest.skipUnless(MIGRATION.is_file(), "region-migration.json not generated yet")
class TestRegionMigrationArtifact(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mig = json.loads(MIGRATION.read_text(encoding="utf-8"))
        cls.cg = json.loads(COUNTRY_GROUPS.read_text(encoding="utf-8"))
        cls.rmap = json.loads(REGION_MAP.read_text(encoding="utf-8"))

    def test_schemas(self):
        self.assertEqual(self.mig["schema"], "navi-mwm-region-migration/v1")
        self.assertEqual(self.cg["schema"], "navi-mwm-country-groups/v1")
        self.assertEqual(self.rmap["schema"], "navi-mwm-region-leaf-mapping/v2")
        self.assertTrue(self.mig["validation"]["every_region_has_leaf"])
        self.assertTrue(self.mig["validation"]["every_routable_leaf_in_country_groups_unique"])

    def test_ostlandet_spot(self):
        ost = self.mig["regions"]["europe_norway_ostlandet"]
        leaves = {x["leaf"] for x in ost["comaps_leaves"]}
        for need in ("Norway_Hedmark", "Norway_Oppland", "Norway_Southern", "Norway_Central"):
            self.assertIn(need, leaves)
        # Spike 3 border leaves (Denmark / Sweden / Western)
        self.assertTrue(
            leaves & {"Denmark_North Denmark Region", "Sweden_Bergslagen", "Norway_Western"}
        )
        self.assertLess(ost["uncovered_pct"], 1.0)

    def test_sorlandet_shares_southern(self):
        sor = self.mig["regions"]["europe_norway_sorlandet"]
        leaves = {x["leaf"] for x in sor["comaps_leaves"]}
        self.assertIn("Norway_Southern", leaves)
        shared = self.mig["shared_leaves"].get("Norway_Southern") or []
        self.assertIn("europe_norway_ostlandet", shared)
        self.assertIn("europe_norway_sorlandet", shared)

    def test_florida_us_state(self):
        fl = self.mig["regions"]["north_america_us_florida"]
        leaves = [x["leaf"] for x in fl["comaps_leaves"]]
        self.assertTrue(any(x.startswith("US_Florida_") for x in leaves))
        self.assertGreaterEqual(len(leaves), 3)

    def test_vietnam_malaysia_split(self):
        vn = {x["leaf"] for x in self.mig["regions"]["asia_vietnam"]["comaps_leaves"]}
        self.assertIn("Vietnam_North", vn)
        self.assertIn("Vietnam_South", vn)
        my = {
            x["leaf"]
            for x in self.mig["regions"]["asia_malaysia_singapore_brunei"]["comaps_leaves"]
        }
        self.assertIn("Malaysia_East", my)
        self.assertIn("Malaysia_West", my)

    def test_country_groups_norway_overrides(self):
        norway = next(c for c in self.cg["countries"] if c["id"] == "Norway")
        by_id = {x["id"]: x for x in norway["leaves"]}
        self.assertEqual(by_id["Norway_Hedmark"]["display_name"], "Innlandet (Hedmark)")
        self.assertTrue(by_id["Norway_Hedmark"]["display_name_overridden"])
        self.assertEqual(by_id["Norway_Oppland"]["display_name"], "Innlandet (Oppland)")
        # Vietnam / Malaysia splits present once each
        vn = next(c for c in self.cg["countries"] if c["id"] == "Vietnam")
        self.assertEqual(
            [x["id"] for x in vn["leaves"]], ["Vietnam_North", "Vietnam_South"]
        )
        my = next(c for c in self.cg["countries"] if c["id"] == "Malaysia")
        self.assertEqual(
            [x["id"] for x in my["leaves"]], ["Malaysia_East", "Malaysia_West"]
        )

    def test_region_map_aligned(self):
        rtl = self.rmap["navi_region_to_comaps_leaves"]
        self.assertEqual(len(rtl), self.mig["region_count"])
        self.assertEqual(
            [x["leaf"] for x in rtl["europe_norway_ostlandet"]["comaps_leaves"]],
            [x["leaf"] for x in self.mig["regions"]["europe_norway_ostlandet"]["comaps_leaves"]],
        )


if __name__ == "__main__":
    unittest.main()
