#!/usr/bin/env python3
"""Tests for countries.txt Ed25519 verify and parallel leaf scheduler."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from comaps_release import (  # noqa: E402
    COUNTRIES_TXT_PUBKEY_HEX,
    decode_hex32,
    verify_countries_txt_sig,
)
from leaf_scheduler import (  # noqa: E402
    default_process_concurrency,
    leaf_sort_key,
    order_leaves,
    pending_leaves,
    save_processed_atomic,
    load_processed,
)


class TestCountriesTxtSignature(unittest.TestCase):
    def test_pubkey_hex_matches_comaps_private_h(self):
        # Pinned: comaps/private.h COUNTRIES_TXT_SIGNATURE_HEX
        self.assertEqual(
            COUNTRIES_TXT_PUBKEY_HEX,
            "91c0a9f6aa182371f047e256ab46489211acc2b51b13197fbe8c94eaa9749c7b",
        )
        self.assertEqual(len(decode_hex32(COUNTRIES_TXT_PUBKEY_HEX)), 32)

    def test_valid_and_tampered(self):
        from nacl.signing import SigningKey

        sk = SigningKey.generate()
        vk = sk.verify_key
        msg = b'{"id":"Countries","v":261105,"g":[]}'
        sig = sk.sign(msg).signature
        pubkey_hex = vk.encode().hex()
        self.assertTrue(verify_countries_txt_sig(msg, sig, pubkey_hex=pubkey_hex))
        tampered = msg.replace(b"261105", b"261106")
        self.assertFalse(verify_countries_txt_sig(tampered, sig, pubkey_hex=pubkey_hex))
        bad_sig = bytes(64)  # wrong
        self.assertFalse(verify_countries_txt_sig(msg, bad_sig, pubkey_hex=pubkey_hex))
        self.assertFalse(verify_countries_txt_sig(msg, sig[:32], pubkey_hex=pubkey_hex))


class TestLeafScheduler(unittest.TestCase):
    def test_order_nordic_europe_world(self):
        leaves = [
            "World",
            "Germany_Bavaria",
            "Norway_Hedmark",
            "Sweden_Stockholm",
            "France_Corsica",
        ]
        ordered = order_leaves(leaves)
        self.assertEqual(ordered[0], "Norway_Hedmark")
        self.assertIn("Sweden_Stockholm", ordered[:3])
        self.assertEqual(ordered[-1], "World")
        self.assertLess(leaf_sort_key("Norway_Hedmark")[0], leaf_sort_key("Germany_Bavaria")[0])
        self.assertLess(leaf_sort_key("Germany_Bavaria")[0], leaf_sort_key("World")[0])

    def test_default_concurrency_headroom(self):
        # 41 GiB available, 16 headroom, 8.2/leaf → floor(25/8.2)=3
        self.assertEqual(
            default_process_concurrency(
                per_leaf_gib=8.2, headroom_gib=16.0, available_gib=41.0
            ),
            3,
        )
        self.assertEqual(
            default_process_concurrency(
                per_leaf_gib=8.2, headroom_gib=16.0, available_gib=20.0
            ),
            1,
        )

    def test_resume_processed_json(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "processed.json"
            save_processed_atomic(
                p,
                {
                    "release_version": 261105,
                    "leaves": {"Norway_Hedmark": {"mwm_bytes": 1}},
                },
            )
            st = load_processed(p)
            pending = pending_leaves(
                ["Norway_Hedmark", "Norway_Oppland", "World"],
                st,
            )
            # World is non-routable and excluded from sidecar processing.
            self.assertEqual(pending, ["Norway_Oppland"])


if __name__ == "__main__":
    unittest.main()
