"""Frozen .rkyv marker + bake-timer detection for MWM schedule guard."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
LIB = ROOT / "scripts" / "lib"
MWM_LIB = ROOT / "scripts" / "mwm" / "lib"
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(MWM_LIB))

from leaf_scheduler import ScheduleGuardConfig, evaluate_schedule_guard  # noqa: E402
from rkyv_freeze import (  # noqa: E402
    RkyvFrozenError,
    is_rkyv_frozen,
    refuse_if_frozen,
    rkyv_bake_timer_enabled,
)


class TestRkyvFreeze(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("NAVI_RKYV_ALLOW_FROZEN", None)
        os.environ.pop("NAVI_MWM_RKYV_BAKE_TIMER_ENABLED", None)
        os.environ.pop("NAVI_PUBLISHED_DIR", None)

    def test_refuse_when_marker_present(self):
        with tempfile.TemporaryDirectory() as td:
            pub = Path(td)
            (pub / ".navi-rkyv-frozen").write_text("frozen\n")
            self.assertTrue(is_rkyv_frozen(pub))
            with self.assertRaises(RkyvFrozenError):
                refuse_if_frozen(pub, entry="run-weekly.sh")

    def test_allow_override_env(self):
        with tempfile.TemporaryDirectory() as td:
            pub = Path(td)
            (pub / ".navi-rkyv-frozen").write_text("frozen\n")
            os.environ["NAVI_RKYV_ALLOW_FROZEN"] = "1"
            refuse_if_frozen(pub, entry="cleanup.sh")

    def test_allow_override_flag(self):
        with tempfile.TemporaryDirectory() as td:
            pub = Path(td)
            (pub / ".navi-rkyv-frozen").write_text("frozen\n")
            refuse_if_frozen(pub, entry="publish-packs.sh", allow_override=True)

    def test_no_marker_ok(self):
        with tempfile.TemporaryDirectory() as td:
            refuse_if_frozen(Path(td), entry="cleanup.sh")

    def test_bake_timer_env_override(self):
        os.environ["NAVI_MWM_RKYV_BAKE_TIMER_ENABLED"] = "0"
        self.assertFalse(rkyv_bake_timer_enabled())
        os.environ["NAVI_MWM_RKYV_BAKE_TIMER_ENABLED"] = "1"
        self.assertTrue(rkyv_bake_timer_enabled())

    def test_bake_timer_false_when_frozen(self):
        with tempfile.TemporaryDirectory() as td:
            pub = Path(td)
            (pub / ".navi-rkyv-frozen").write_text("frozen\n")
            os.environ.pop("NAVI_MWM_RKYV_BAKE_TIMER_ENABLED", None)
            self.assertFalse(rkyv_bake_timer_enabled(published_dir=pub))

    def test_schedule_guard_skips_window_when_timer_inactive(self):
        cfg = ScheduleGuardConfig(
            enabled=True,
            weekly_weekday_utc=0,
            weekly_hour_utc=0,
            weekly_minute_utc=0,
            pause_before_hours=6.0,
            pause_after_hours=6.0,
        )
        with mock.patch(
            "leaf_scheduler._rkyv_bake_timer_active_for_guard",
            return_value=False,
        ):
            # Monday window that would normally pause
            p, reason, _ = evaluate_schedule_guard(
                datetime(2026, 11, 16, 1, 0, tzinfo=timezone.utc), cfg
            )
            self.assertFalse(p)
            self.assertEqual(reason, "rkyv_bake_timer_inactive")

    def test_schedule_guard_still_pauses_on_lock_when_timer_inactive(self):
        with tempfile.TemporaryDirectory() as td:
            lock_file = Path(td) / "weekly.lock"
            lock_file.write_text("")
            cfg = ScheduleGuardConfig(
                enabled=True,
                weekly_weekday_utc=0,
                weekly_hour_utc=0,
                weekly_minute_utc=0,
                pause_before_hours=6.0,
                pause_after_hours=6.0,
                lock_path=lock_file,
            )
            import fcntl

            with lock_file.open("r+") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                with mock.patch(
                    "leaf_scheduler._rkyv_bake_timer_active_for_guard",
                    return_value=False,
                ):
                    p, reason, _ = evaluate_schedule_guard(
                        datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc), cfg
                    )
                    self.assertTrue(p, reason)
                    self.assertIn("weekly_bake_lock_held", reason)
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    unittest.main()
