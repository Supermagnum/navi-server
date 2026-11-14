"""Validate docs/examples/catalog-v2.example.json against navi-mwm-catalog/v2 rules."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "docs" / "examples" / "catalog-v2.example.json"

REQUIRED_TOP = {
    "schema",
    "mode",
    "version",
    "comaps",
    "generator",
    "limitation",
    "leaves",
    "region_to_leaves",
    "shared_leaves",
    "world_leaf_count",
}

REQUIRED_COMAPS = {
    "map_series",
    "release_version",
    "countries_txt_sha256",
    "countries_txt_bytes",
    "countries_txt_sig_verified",
    "countries_txt_sig_sha256",
    "countries_txt_pubkey_hex",
    "metaserver_url",
    "maps_json_url",
    "note",
}

REQUIRED_LEAF = {
    "id",
    "sidecar_url",
    "sidecar_bytes",
    "sidecar_sha256",
    "comaps_file",
    "comaps_bytes",
    "comaps_sha1_base64",
    "mwm_bytes",
    "mwm_sha256",
    "mwm_fingerprint",
    "shared",
}


def _is_sha256_hex(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        c in "0123456789abcdef" for c in value
    )


class TestCatalogV2Example(unittest.TestCase):
    def test_example_matches_v2_schema(self):
        self.assertTrue(EXAMPLE.is_file(), f"missing {EXAMPLE}")
        cat = json.loads(EXAMPLE.read_text())
        self.assertEqual(REQUIRED_TOP, set(cat.keys()) & REQUIRED_TOP)
        missing = REQUIRED_TOP - set(cat.keys())
        self.assertFalse(missing, f"missing top-level keys: {sorted(missing)}")

        self.assertEqual(cat["schema"], "navi-mwm-catalog/v2")
        self.assertEqual(cat["mode"], "comaps-release")
        self.assertIsInstance(cat["version"], str)
        self.assertTrue(cat["version"].isdigit())
        self.assertIsInstance(cat["limitation"], str)
        self.assertIsInstance(cat["world_leaf_count"], int)
        self.assertGreater(cat["world_leaf_count"], 0)

        comaps = cat["comaps"]
        self.assertFalse(REQUIRED_COMAPS - set(comaps.keys()))
        self.assertTrue(comaps["countries_txt_sig_verified"] is True)
        self.assertTrue(_is_sha256_hex(comaps["countries_txt_sha256"]))
        self.assertTrue(_is_sha256_hex(comaps["countries_txt_sig_sha256"]))
        self.assertEqual(
            comaps["countries_txt_pubkey_hex"],
            "91c0a9f6aa182371f047e256ab46489211acc2b51b13197fbe8c94eaa9749c7b",
        )
        self.assertEqual(comaps["release_version"], int(cat["version"]))

        gen = cat["generator"]
        for k in ("tag", "sha", "role"):
            self.assertIn(k, gen)
            self.assertIsInstance(gen[k], str)

        self.assertIsInstance(cat["region_to_leaves"], dict)
        self.assertIsInstance(cat["shared_leaves"], dict)
        self.assertIsInstance(cat["leaves"], list)
        self.assertGreaterEqual(len(cat["leaves"]), 1)

        for leaf in cat["leaves"]:
            missing_leaf = REQUIRED_LEAF - set(leaf.keys())
            self.assertFalse(missing_leaf, f"{leaf.get('id')}: {sorted(missing_leaf)}")
            self.assertTrue(_is_sha256_hex(leaf["sidecar_sha256"]))
            self.assertTrue(_is_sha256_hex(leaf["mwm_sha256"]))
            self.assertIsInstance(leaf["sidecar_bytes"], int)
            self.assertIsInstance(leaf["mwm_bytes"], int)
            self.assertTrue(leaf["sidecar_url"].endswith(".navi-sidecar.v1.zst"))
            fp = leaf["mwm_fingerprint"]
            self.assertEqual(fp["size"], leaf["mwm_bytes"])
            self.assertEqual(fp["sha256"], leaf["mwm_sha256"])
            self.assertIsInstance(leaf["shared"], bool)
            if leaf["shared"]:
                self.assertIn("shared_with_regions", leaf)
                self.assertIsInstance(leaf["shared_with_regions"], list)


if __name__ == "__main__":
    unittest.main()
