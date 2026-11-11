#!/usr/bin/env python3
"""Publish + retention logic tests (scratch trees only)."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RETAIN = ROOT / "scripts/mwm/retain-mwm.sh"


class RetainTests(unittest.TestCase):
    def test_never_deletes_current(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in ("v1", "v2", "v3", "v4"):
                d = root / name
                d.mkdir()
                (d / "marker").write_text(name)
                time.sleep(0.05)
            (root / "current.json").write_text(json.dumps({"version": "v3"}) + "\n")
            env = os.environ.copy()
            env["NAVI_MWM_GRACE_DAYS"] = "0"
            env["NAVI_MWM_KEEP_RELEASES"] = "2"
            subprocess.check_call(
                ["bash", str(RETAIN), "--publish-root", str(root)],
                env=env,
            )
            self.assertTrue((root / "v3").is_dir())
            # newest keep=2 plus current protected
            remaining = sorted(p.name for p in root.iterdir() if p.is_dir())
            self.assertIn("v3", remaining)
            self.assertIn("v4", remaining)


if __name__ == "__main__":
    unittest.main()
