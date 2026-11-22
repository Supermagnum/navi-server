"""Guard: tiny named-routes PBF must fail closed."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "annotate-named-routes.py"


class AnnotateNamedRoutesMinBytes(unittest.TestCase):
    def test_tiny_pbf_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            tiny = td_path / "tiny.osm.pbf"
            tiny.write_bytes(b"\x00" * 100)
            out = td_path / "out.jsonl"
            report = td_path / "report.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--pbf",
                    str(tiny),
                    "--out-jsonl",
                    str(out),
                    "--report-json",
                    str(report),
                ],
                capture_output=True,
                text=True,
                env={**dict(**{k: v for k, v in __import__("os").environ.items()}),
                     "NAVI_MWM_NAMED_ROUTES_MIN_BYTES": "1000000"},
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("too small", (proc.stderr + proc.stdout).lower())


if __name__ == "__main__":
    unittest.main()
