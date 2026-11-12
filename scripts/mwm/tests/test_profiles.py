#!/usr/bin/env python3
"""Unit tests for MWM generator profiles."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PROFILES = ROOT / "config" / "mwm" / "profiles"


class ProfileTests(unittest.TestCase):
    def test_nordic_and_planet_profiles_exist(self) -> None:
        nordic = json.loads((PROFILES / "nordic.json").read_text())
        planet = json.loads((PROFILES / "planet.json").read_text())
        self.assertEqual(nordic["node_storage"], "map")
        self.assertEqual(planet["node_storage"], "mem")
        self.assertIn("threads_features", nordic)
        self.assertIn("threads_count", nordic)

    def test_apply_profile_shell(self) -> None:
        script = f"""
set -euo pipefail
source "{ROOT}/scripts/mwm/lib/common.sh"
export NAVI_MWM_LEAFSET="{ROOT}/config/mwm/leafsets/nordic.txt"
export NAVI_MWM_NO_PROFILE=0
# Clear then apply
unset NAVI_MWM_NODE_STORAGE NAVI_MWM_THREADS_FEATURES NAVI_MWM_THREADS_COUNT || true
mwm_load_defaults
mwm_apply_profile_from_leafset
echo NODE=$NAVI_MWM_NODE_STORAGE
echo TF=$NAVI_MWM_THREADS_FEATURES
echo TC=$NAVI_MWM_THREADS_COUNT
"""
        out = subprocess.check_output(["bash", "-c", script], text=True)
        self.assertIn("NODE=map", out)
        self.assertIn("TF=4", out)
        self.assertIn("TC=4", out)


if __name__ == "__main__":
    unittest.main()
