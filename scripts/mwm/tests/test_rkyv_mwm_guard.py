""" .rkyv scrub/publish must not touch published/mwm/ (allow-list + marker). """
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "scripts" / "lib"
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(ROOT / "scripts" / "mwm"))

from rkyv_published_guard import (  # noqa: E402
    MWM_OWNED_MARKER,
    RkyvPublishedGuardError,
    assert_rkyv_mutation_allowed,
    ensure_mwm_owned_marker,
    packs_root,
    plan_safe_or_raise,
)


class TestRkyvMwmGuard(unittest.TestCase):
    def test_allow_packs_refuse_mwm(self):
        with tempfile.TemporaryDirectory() as td:
            published = Path(td) / "published"
            packs = packs_root(published)
            packs.mkdir(parents=True)
            (packs / "europe" / "norway").mkdir(parents=True)
            mwm = published / "mwm"
            mwm.mkdir()
            ensure_mwm_owned_marker(mwm)
            (mwm / "261105").mkdir()
            (mwm / "261105" / "x.zst").write_text("x")

            ok = assert_rkyv_mutation_allowed(
                packs / "europe" / "norway", published_dir=published
            )
            self.assertTrue(str(ok).endswith("norway"))

            with self.assertRaises(RkyvPublishedGuardError):
                assert_rkyv_mutation_allowed(mwm / "261105", published_dir=published)

            with self.assertRaises(RkyvPublishedGuardError):
                assert_rkyv_mutation_allowed(
                    packs / ".." / "mwm" / "261105", published_dir=published
                )

            plan = plan_safe_or_raise(
                [packs / "europe" / "norway"], published_dir=published
            )
            self.assertEqual(len(plan), 1)
            self.assertTrue(all("/mwm/" not in p and not p.endswith("/mwm") for p in plan))

    def test_bad_glob_into_mwm_aborts(self):
        with tempfile.TemporaryDirectory() as td:
            published = Path(td) / "published"
            (published / "packs").mkdir(parents=True)
            mwm = published / "mwm"
            mwm.mkdir()
            ensure_mwm_owned_marker(mwm)
            # Simulate a bad path that a glob might produce.
            bad = published / "packs" / ".." / "mwm" / "261105"
            bad.mkdir(parents=True)
            with self.assertRaises(RkyvPublishedGuardError) as ctx:
                assert_rkyv_mutation_allowed(bad, published_dir=published)
            self.assertIn("refusing", str(ctx.exception).lower())

    def test_cleanup_dry_plan_fixture_excludes_mwm(self):
        """Fixture tree: scrub allow-list probe never lists mwm paths."""
        with tempfile.TemporaryDirectory() as td:
            published = Path(td) / "published"
            packs = published / "packs" / "europe" / "norway" / "20260101T000000Z"
            packs.mkdir(parents=True)
            (packs / "manifest.json").write_text("{}")
            mwm = published / "mwm" / "261105"
            mwm.mkdir(parents=True)
            (mwm / "leaf.zst").write_text("sidecar")
            ensure_mwm_owned_marker(published / "mwm")

            # Collect would-be prune targets the way cleanup does (packs only).
            candidates = list((published / "packs").rglob("*"))
            candidates = [p for p in candidates if p.is_dir()]
            planned = []
            for p in candidates:
                try:
                    planned.append(
                        str(assert_rkyv_mutation_allowed(p, published_dir=published))
                    )
                except RkyvPublishedGuardError:
                    self.fail(f"packs path refused unexpectedly: {p}")
            self.assertTrue(planned)
            self.assertTrue(all("/mwm/" not in p for p in planned))

            with self.assertRaises(RkyvPublishedGuardError):
                assert_rkyv_mutation_allowed(mwm, published_dir=published)


class TestCheckOrBakeDetectOnly(unittest.TestCase):
    def test_enabled_zero_does_not_exec_bake(self):
        script = ROOT / "scripts" / "mwm" / "check-or-bake.sh"
        text = script.read_text(encoding="utf-8")
        self.assertIn("NAVI_MWM_ENABLED", text)
        self.assertIn("detect-only", text)
        self.assertIn("run-mwm-bake.sh", text)
        # Bake only after enabled check.
        enabled_idx = text.index('NAVI_MWM_ENABLED}" != "1"')
        bake_idx = text.index("run-mwm-bake.sh")
        self.assertLess(enabled_idx, bake_idx)

    def test_systemd_unit_uses_check_or_bake(self):
        unit = ROOT / "systemd" / "navi-mwm-check.service"
        text = unit.read_text(encoding="utf-8")
        self.assertIn("check-or-bake.sh", text)
        self.assertNotIn("if [ $ec -eq 10 ]; then exec", text)
        self.assertIn("NAVI_MWM_ENABLED=0", text)


if __name__ == "__main__":
    unittest.main()
