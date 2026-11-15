"""Pin Hedmark worked-example docs to a checked-in live sidecar fixture.

Fails CI when docs/examples/catalog-v2.example.json or sidecar-format.md
silently drift from docs/examples/fixtures/Norway_Hedmark.*.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
import unittest
from pathlib import Path

import zstandard as zstd

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "docs" / "examples" / "fixtures" / "Norway_Hedmark.navi-sidecar.v1.zst"
META = ROOT / "docs" / "examples" / "fixtures" / "Norway_Hedmark.sidecar.meta.json"
CATALOG_EXAMPLE = ROOT / "docs" / "examples" / "catalog-v2.example.json"
SIDECAR_DOC = ROOT / "docs" / "sidecar-format.md"
FETCH_DOC = ROOT / "docs" / "client-fetch-mwm.md"
COUNTRIES_EXCERPT = ROOT / "docs" / "examples" / "countries-txt.excerpt.json"


class TestHedmarkSidecarDocs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meta = json.loads(META.read_text())
        cls.raw = FIXTURE.read_bytes()

    def test_fixture_matches_meta_checksum(self):
        self.assertTrue(FIXTURE.is_file(), f"missing {FIXTURE}")
        self.assertEqual(len(self.raw), self.meta["sidecar_bytes"])
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), self.meta["sidecar_sha256"])

    def test_catalog_example_hedmark_matches_fixture(self):
        cat = json.loads(CATALOG_EXAMPLE.read_text())
        leaf = next(x for x in cat["leaves"] if x["id"] == "Norway_Hedmark")
        self.assertEqual(leaf["sidecar_bytes"], self.meta["sidecar_bytes"])
        self.assertEqual(leaf["sidecar_sha256"], self.meta["sidecar_sha256"])
        self.assertEqual(leaf["mwm_sha256"], self.meta["inspect"]["mwm_sha256"])
        self.assertEqual(leaf["mwm_bytes"], self.meta["inspect"]["mwm_size"])
        self.assertEqual(leaf["mwm_fingerprint"]["sha256"], self.meta["inspect"]["mwm_sha256"])
        self.assertEqual(leaf["mwm_fingerprint"]["size"], self.meta["inspect"]["mwm_size"])
        # country_groups Norway_Hedmark rollup
        norway = next(g for g in cat["country_groups"] if g["id"] == "Norway")
        cg_leaf = next(x for x in norway["leaves"] if x["id"] == "Norway_Hedmark")
        self.assertEqual(cg_leaf["sidecar_bytes"], self.meta["sidecar_bytes"])

    def test_sidecar_format_doc_cites_fixture_sizes(self):
        text = SIDECAR_DOC.read_text()
        self.assertIn(str(self.meta["sidecar_bytes"]), text)
        self.assertIn(self.meta["sidecar_sha256"], text)
        self.assertIn(str(self.meta["logical_bytes_after_outer_zst"]), text)
        self.assertNotIn("261797", text)
        self.assertNotIn("593e2ec42bf6827763a4707b356d80f4e43d993c68a4a8ee93cac094a033d200", text)
        # section sizes table
        for sec in self.meta["section_sizes"]:
            self.assertIn(str(sec["uncompressed"]), text)
            self.assertIn(str(sec["compressed"]), text)

    def test_inspect_counts_match_meta(self):
        blob = zstd.ZstdDecompressor().decompress(self.raw)
        self.assertEqual(len(blob), self.meta["logical_bytes_after_outer_zst"])
        self.assertEqual(blob[:4], b"NVSC")
        # payload starts after directory; meta pins offset
        payload_start = self.meta["payload_start_offset"]
        self.assertEqual(payload_start, 377)
        # wetland section
        wet_meta = next(s for s in self.meta["section_sizes"] if s["name"] == "wetland_edges")
        wet_comp = blob[payload_start : payload_start + wet_meta["compressed"]]
        wet = zstd.ZstdDecompressor().decompress(wet_comp)
        self.assertEqual(len(wet), wet_meta["uncompressed"])
        count = struct.unpack_from("<I", wet, 0)[0]
        self.assertEqual(count, self.meta["inspect"]["wetland_records"])
        for rec in self.meta["first_wetland_records"]:
            base = 4 + rec["n"] * 8
            fid, seg, dire, klass = struct.unpack_from("<IHBB", wet, base)
            self.assertEqual(fid, rec["feature_id"])
            self.assertEqual(seg, rec["segment"])
            self.assertEqual(dire, rec["dir"])
            self.assertEqual(klass, 1)
            pretty = (
                f"{wet[base:base+4].hex()} {wet[base+4:base+6].hex()} "
                f"{wet[base+6:base+7].hex()} {wet[base+7:base+8].hex()}"
            )
            self.assertEqual(pretty, rec["hex"])

    def test_client_fetch_doc_uses_public_mwm_base(self):
        text = FETCH_DOC.read_text()
        self.assertIn("https://navigate-me.duckdns.org/mwm/", text)
        # Client URL examples must not teach /published/mwm/
        for line in text.splitlines():
            if line.lstrip().startswith("|") and "GET" in line and "published/mwm" in line:
                self.fail(f"client GET example still uses /published/mwm/: {line}")
        # Explicit note that /published/mwm/ 404s is fine
        self.assertRegex(text, re.compile(r"/published/mwm/.*404", re.I | re.S))
        self.assertIn("JSON array", text)
        self.assertIn("countries-txt.excerpt.json", text)

    def test_countries_excerpt_shape(self):
        excerpt = json.loads(COUNTRIES_EXCERPT.read_text())
        self.assertEqual(excerpt["id"], "Countries")
        self.assertEqual(excerpt["v"], 261105)
        self.assertIn("g", excerpt)
        # find Hedmark leaf fields the client uses
        found = False

        def walk(node):
            nonlocal found
            if isinstance(node, dict):
                if node.get("id") == "Norway_Hedmark":
                    self.assertIn("s", node)
                    self.assertIn("sha1_base64", node)
                    found = True
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(excerpt)
        self.assertTrue(found, "excerpt missing Norway_Hedmark leaf")


if __name__ == "__main__":
    unittest.main()
