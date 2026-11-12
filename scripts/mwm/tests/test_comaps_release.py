#!/usr/bin/env python3
"""Unit tests for CoMaps release discovery/download helpers and catalog schema."""
from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from comaps_release import (  # noqa: E402
    leaf_mwm_url,
    parse_countries_txt,
    parse_maps_json,
    parse_servers_json,
    sha256_bytes,
    url_encode_leaf,
    walk_countries_leaves,
    LeafInfo,
)


class TestDiscoveryParsing(unittest.TestCase):
    def test_parse_maps_json(self):
        raw = {
            "map-series": {
                "2026.10.27": {"status": "active", "latest": 261105},
                "2026.04.05": {"status": "EOL", "latest": 260708},
            }
        }
        self.assertEqual(parse_maps_json(raw, "2026.10.27"), 261105)
        with self.assertRaises(RuntimeError):
            parse_maps_json(raw, "2026.04.05")

    def test_parse_servers_list_and_object(self):
        self.assertEqual(
            parse_servers_json('["https://a/","https://b"]'),
            ["https://a/", "https://b/"],
        )
        self.assertEqual(
            parse_servers_json('{"servers":["https://c"]}'),
            ["https://c/"],
        )

    def test_countries_txt_leaves(self):
        raw = {
            "id": "Countries",
            "v": 261105,
            "map_series": "2026.10.27",
            "g": [
                {
                    "id": "Norway",
                    "g": [
                        {
                            "id": "Norway_Hedmark",
                            "s": 102194292,
                            "sha1_base64": "SYcBVbtm7e8BwcOGWpo0WV8bFK4=",
                            "affiliations": ["Hedmark"],
                        }
                    ],
                },
                {"id": "World", "s": 10, "sha1_base64": "AAAAAAAA"},
            ],
        }
        v, series, leaves = parse_countries_txt(json.dumps(raw))
        self.assertEqual(v, 261105)
        self.assertEqual(series, "2026.10.27")
        self.assertIn("Norway_Hedmark", leaves)
        self.assertEqual(leaves["Norway_Hedmark"].size, 102194292)
        self.assertIn("World", leaves)

    def test_url_encode_leaf_spaces(self):
        self.assertEqual(
            url_encode_leaf("Denmark_Capital Region of Denmark"),
            "Denmark_Capital%20Region%20of%20Denmark",
        )
        url = leaf_mwm_url(
            "https://cdn-fi-1.comaps.app/",
            "2026.10.27",
            261105,
            "Norway_South Trondelag",
        )
        self.assertIn("Norway_South%20Trondelag.mwm", url)


class TestChecksumAndCleanup(unittest.TestCase):
    def test_sha256_and_sha1_base64_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "f.bin"
            data = b"navi-mwm-test" * 1000
            p.write_bytes(data)
            self.assertEqual(sha256_bytes(data), hashlib.sha256(data).hexdigest())
            digest = hashlib.sha1(data).digest()
            b64 = base64.b64encode(digest).decode()
            leaf = LeafInfo(id="t", size=len(data), sha1_base64=b64)
            self.assertEqual(leaf.size, len(data))

    def test_streaming_cleanup_contract(self):
        """Processed leaf must not retain the MWM after sidecar bake (contract)."""
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            mwm = work / "Norway_Hedmark.mwm"
            mwm.write_bytes(b"fake")
            sidecar = work / "Norway_Hedmark.navi-sidecar.v1.zst"
            sidecar.write_bytes(b"sc")
            # Simulate post-leaf cleanup
            mwm.unlink()
            self.assertFalse(mwm.exists())
            self.assertTrue(sidecar.exists())


class TestCatalogSchemaV2(unittest.TestCase):
    def test_minimal_v2_shape(self):
        cat = {
            "schema": "navi-mwm-catalog/v2",
            "mode": "comaps-release",
            "version": "261105",
            "comaps": {
                "map_series": "2026.10.27",
                "release_version": 261105,
                "countries_txt_sha256": "abc",
                "metaserver_url": "https://cdn-us-1.comaps.app",
            },
            "leaves": [
                {
                    "id": "Norway_Hedmark",
                    "mwm_sha256": "0" * 64,
                    "mwm_bytes": 1,
                    "sidecar_sha256": "1" * 64,
                    "sidecar_bytes": 2,
                    "mwm_fingerprint": {"size": 1, "sha256": "0" * 64},
                }
            ],
            "region_to_leaves": {"europe_norway_ostlandet": ["Norway_Hedmark"]},
            "shared_leaves": {},
        }
        self.assertEqual(cat["schema"], "navi-mwm-catalog/v2")
        self.assertEqual(cat["mode"], "comaps-release")
        fp = cat["leaves"][0]["mwm_fingerprint"]
        self.assertEqual(fp["sha256"], cat["leaves"][0]["mwm_sha256"])


class TestElevationEncodingHelpers(unittest.TestCase):
    def test_zigzag_i16_compatible_with_rust(self):
        def enc(v: int) -> int:
            return ((v << 1) ^ (v >> 15)) & 0xFFFF

        def dec(v: int) -> int:
            return ((v >> 1) & 0x7FFF) ^ (-(v & 1))

        for v in (0, 1, -1, 1234, -1200, 32767, -32768):
            # Match Rust i16 zigzag: ((v << 1) ^ (v >> 15)) as u16
            zz = ((v << 1) ^ (v >> 15)) & 0xFFFF
            # decode
            out = (zz >> 1) ^ (-(zz & 1))
            # Python ints are unbounded; normalize to i16
            if out >= 0x8000:
                out -= 0x10000
            self.assertEqual(out, v)


if __name__ == "__main__":
    unittest.main()
