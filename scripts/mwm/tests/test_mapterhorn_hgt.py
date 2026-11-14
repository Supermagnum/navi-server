#!/usr/bin/env python3
"""HGT conversion tests with a tiny synthetic fixture (void-fill path)."""
from __future__ import annotations

import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HGT = ROOT / "scripts/mwm/mapterhorn-to-hgt.py"
HGT_N = 3601
VOID = -32768
CELL_BYTES = HGT_N * HGT_N * 2


class HgtTests(unittest.TestCase):
    def test_void_fill_cell_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            t = Path(td)
            out = t / "hgt"
            man = t / "manifest.json"
            attr = t / "attribution.json"
            # Local attribution fixture (Mapterhorn sources shape)
            local_attr = t / "sources.json"
            local_attr.write_text(
                json.dumps([{"source": "nos", "name": "Norway DEM fixture", "license": "CC-BY"}])
                + "\n"
            )
            subprocess.check_call(
                [
                    "python3",
                    str(HGT),
                    "--out",
                    str(out),
                    "--manifest",
                    str(man),
                    "--attribution-out",
                    str(attr),
                    "--attribution-json",
                    str(local_attr),
                    "--bbox",
                    "10.1,60.1,10.9,60.9",
                    "--allow-void-fill",
                ]
            )
            cell = out / "N60E010.hgt"
            self.assertTrue(cell.is_file())
            self.assertEqual(cell.stat().st_size, CELL_BYTES)
            link = out / "N60E010.SRTMGL1.hgt"
            self.assertTrue(link.is_symlink())
            raw = cell.read_bytes()
            self.assertEqual(struct.unpack_from(">h", raw, 0)[0], VOID)
            meta = json.loads(attr.read_text())
            self.assertEqual(meta["label"], "Mapterhorn")
            self.assertIn("sources", meta)
            blob = json.dumps(meta).lower()
            self.assertNotEqual(blob.strip(), '"elevation data"')

            # Second run should skip rebuild (pending=0)
            subprocess.check_call(
                [
                    "python3",
                    str(HGT),
                    "--out",
                    str(out),
                    "--manifest",
                    str(man),
                    "--attribution-out",
                    str(attr),
                    "--attribution-json",
                    str(local_attr),
                    "--bbox",
                    "10.1,60.1,10.9,60.9",
                    "--allow-void-fill",
                ]
            )
            mtime1 = cell.stat().st_mtime_ns
            # fingerprint unchanged → file untouched
            self.assertEqual(cell.stat().st_mtime_ns, mtime1)


if __name__ == "__main__":
    unittest.main()
