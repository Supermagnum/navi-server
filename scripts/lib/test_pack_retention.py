#!/usr/bin/env python3
"""Unit tests for pack_retention (fixtures under a temp published tree)."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from pack_retention import (
    apply_plan,
    build_plan,
    parse_gen_publish_unix,
)


def _write_gen(region_dir: Path, gen: str, *, payload: str = "x") -> Path:
    g = region_dir / gen
    g.mkdir(parents=True)
    (g / "manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "generation": gen,
                "region_id": region_dir.name,
                "files": {"blob.bin": {"sha256": "a" * 64, "bytes": len(payload)}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (g / "checksums.sha256").write_text(f"{'a' * 64}  blob.bin\n", encoding="utf-8")
    (g / "blob.bin").write_text(payload, encoding="utf-8")
    return g


def _write_current(published: Path, regions: dict[str, str]) -> None:
    body = {
        "schema": 1,
        "generation": "test",
        "created_unix": 0,
        "packs_base": "/packs",
        "layout": "geofabrik-path",
        "regions": [
            {
                "region_id": rid,
                "generation": gen,
                "manifest_url": f"/packs/{rid}/{gen}/manifest.json",
            }
            for rid, gen in regions.items()
        ],
    }
    (published / "current.json").write_text(
        json.dumps(body, indent=2) + "\n", encoding="utf-8"
    )


class PackRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pack-retention-"))
        self.published = self.tmp / "published"
        self.packs = self.published / "packs"
        self.packs.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_gen_publish_unix(self) -> None:
        ts = parse_gen_publish_unix("20261001T181517Z-625025-africa_mali-493b9dff")
        self.assertIsNotNone(ts)
        self.assertEqual(parse_gen_publish_unix("not-a-gen"), None)

    def test_grace_within_window_keeps_prior(self) -> None:
        region = self.packs / "europe" / "andorra"
        old = "20260920T000000Z-1-old"
        new = "20260925T000000Z-2-new"
        _write_gen(region, old, payload="old")
        _write_gen(region, new, payload="new")
        _write_current(self.published, {"europe/andorra": new})
        # Now = new publish + 3 days (< 7).
        now = parse_gen_publish_unix(new) + 3 * 86400
        plan = build_plan(
            self.published, mode="grace", keep=2, grace_days=7, now_unix=now
        )
        self.assertIsNone(plan.abort_reason)
        self.assertEqual(plan.deletions, [])
        self.assertTrue((region / old).is_dir())
        self.assertTrue((region / new).is_dir())

    def test_grace_outside_window_deletes_prior(self) -> None:
        region = self.packs / "europe" / "andorra"
        old = "20260901T000000Z-1-old"
        new = "20260910T000000Z-2-new"
        _write_gen(region, old, payload="old")
        _write_gen(region, new, payload="new")
        _write_current(self.published, {"europe/andorra": new})
        now = parse_gen_publish_unix(new) + 10 * 86400
        plan = build_plan(
            self.published, mode="grace", keep=2, grace_days=7, now_unix=now
        )
        self.assertEqual(len(plan.deletions), 1)
        self.assertEqual(plan.deletions[0].generation, old)
        self.assertIn("grace_elapsed", plan.deletions[0].reason)
        apply_plan(plan, dry_run=False, log=lambda *_a, **_k: None)
        self.assertFalse((region / old).is_dir())
        self.assertTrue((region / new).is_dir())

    def test_only_current_no_delete(self) -> None:
        region = self.packs / "europe" / "andorra"
        cur = "20260910T000000Z-1-only"
        _write_gen(region, cur)
        _write_current(self.published, {"europe/andorra": cur})
        plan = build_plan(
            self.published, mode="grace", keep=2, grace_days=7, now_unix=1_800_000_000
        )
        self.assertEqual(plan.deletions, [])

    def test_cap_exceeded_inside_grace(self) -> None:
        region = self.packs / "europe" / "andorra"
        g1 = "20260901T000000Z-1"
        g2 = "20260902T000000Z-2"
        g3 = "20260903T000000Z-3"
        for g in (g1, g2, g3):
            _write_gen(region, g, payload=g)
        _write_current(self.published, {"europe/andorra": g3})
        # All within grace relative to now = g3 + 1 day.
        now = parse_gen_publish_unix(g3) + 86400
        plan = build_plan(
            self.published, mode="grace", keep=2, grace_days=7, now_unix=now
        )
        self.assertEqual(len(plan.deletions), 1)
        self.assertEqual(plan.deletions[0].generation, g1)
        self.assertIn("cap_exceeds_keep", plan.deletions[0].reason)

    def test_missing_current_json_aborts(self) -> None:
        region = self.packs / "europe" / "andorra"
        _write_gen(region, "20260910T000000Z-1")
        plan = build_plan(
            self.published, mode="grace", keep=2, grace_days=7, now_unix=1_800_000_000
        )
        self.assertIsNotNone(plan.abort_reason)
        self.assertEqual(plan.deletions, [])

    def test_missing_current_manifest_skips_region(self) -> None:
        region = self.packs / "europe" / "andorra"
        old = "20260901T000000Z-1-old"
        new = "20260910T000000Z-2-new"
        _write_gen(region, old)
        gnew = _write_gen(region, new)
        (gnew / "manifest.json").unlink()
        _write_current(self.published, {"europe/andorra": new})
        now = parse_gen_publish_unix(new) + 30 * 86400
        plan = build_plan(
            self.published, mode="grace", keep=2, grace_days=7, now_unix=now
        )
        self.assertEqual(plan.deletions, [])
        self.assertEqual(len(plan.skips), 1)
        self.assertIn("manifest.json missing", plan.skips[0].reason)
        self.assertTrue((region / old).is_dir())

    def test_dry_run_makes_no_changes(self) -> None:
        region = self.packs / "europe" / "andorra"
        old = "20260901T000000Z-1-old"
        new = "20260910T000000Z-2-new"
        _write_gen(region, old)
        _write_gen(region, new)
        _write_current(self.published, {"europe/andorra": new})
        now = parse_gen_publish_unix(new) + 30 * 86400
        plan = build_plan(
            self.published, mode="grace", keep=2, grace_days=7, now_unix=now
        )
        self.assertEqual(len(plan.deletions), 1)
        apply_plan(plan, dry_run=True, log=lambda *_a, **_k: None)
        self.assertTrue((region / old).is_dir())
        self.assertTrue((region / new).is_dir())

    def test_orphan_region_left_alone(self) -> None:
        orphan = self.packs / "legacy" / "enfield"
        old = "20260101T000000Z-orphan"
        cur = "20260910T000000Z-live"
        _write_gen(orphan, old)
        live = self.packs / "europe" / "andorra"
        _write_gen(live, cur)
        _write_current(self.published, {"europe/andorra": cur})
        plan = build_plan(
            self.published, mode="grace", keep=2, grace_days=7, now_unix=1_800_000_000
        )
        self.assertTrue((orphan / old).is_dir())
        kinds = {r.kind for r in plan.reports}
        self.assertIn("orphan_or_retired_region", kinds)

    def test_count_mode_keeps_newest_n(self) -> None:
        region = self.packs / "europe" / "andorra"
        gens = [
            "20260901T000000Z-1",
            "20260902T000000Z-2",
            "20260903T000000Z-3",
        ]
        for g in gens:
            _write_gen(region, g)
        _write_current(self.published, {"europe/andorra": gens[-1]})
        plan = build_plan(
            self.published, mode="count", keep=2, grace_days=7, now_unix=1_800_000_000
        )
        self.assertEqual([d.generation for d in plan.deletions], [gens[0]])


if __name__ == "__main__":
    unittest.main()
