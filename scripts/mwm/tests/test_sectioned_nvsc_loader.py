#!/usr/bin/env python3
"""Sectioned NVSC wetland section is what the routes_builder hook must read."""
from __future__ import annotations

import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SIDECAR_CRATE = ROOT / "crates" / "navi-mwm-sidecar"


class TestSectionedNvscLoader(unittest.TestCase):
    def test_bake_inspect_has_wetland_section(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            mwm = td / "leaf.mwm"
            mwm.write_bytes(b"fake-mwm-bytes-for-fingerprint")
            wet = td / "wet.jsonl"
            wet.write_text(
                json.dumps(
                    {
                        "feature_id": 42,
                        "segment_index": 1,
                        "direction": 0,
                        "class": 1,
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "feature_id": 42,
                        "segment_index": 1,
                        "direction": 1,
                        "class": 1,
                    }
                )
                + "\n"
            )
            bw = td / "bw.jsonl"
            bw.write_text("")
            elev = td / "elev.jsonl"
            elev.write_text("")
            attr = td / "attr.json"
            attr.write_text(
                json.dumps(
                    {
                        "Mapterhorn": True,
                        "OpenStreetMap": True,
                        "CoMaps": True,
                    }
                )
            )
            out = td / "leaf.navi-sidecar.v1.zst"
            proc = subprocess.run(
                [
                    "cargo",
                    "run",
                    "-q",
                    "-p",
                    "navi-mwm-sidecar",
                    "--release",
                    "--",
                    "bake",
                    "--mwm",
                    str(mwm),
                    "--wetland-jsonl",
                    str(wet),
                    "--boardwalk-jsonl",
                    str(bw),
                    "--elevation-jsonl",
                    str(elev),
                    "--attribution",
                    str(attr),
                    "--generator-tag",
                    "test",
                    "--generator-sha",
                    "deadbeef",
                    "--out",
                    str(out),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            insp = subprocess.run(
                [
                    "cargo",
                    "run",
                    "-q",
                    "-p",
                    "navi-mwm-sidecar",
                    "--release",
                    "--",
                    "inspect",
                    "--sidecar",
                    str(out),
                ],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=True,
            )
            info = json.loads(insp.stdout)
            self.assertIn("wetland_edges", info.get("sections") or [])
            self.assertEqual(info.get("soft_records"), 2)
            # Decode wetland_edges body via Python zstd (hook contract).
            recs = self._read_wetland_section(out)
            self.assertEqual(len(recs), 2)
            self.assertEqual(recs[0]["feature_id"], 42)
            self.assertEqual(recs[0]["class"], 1)

    def _read_wetland_section(self, path: Path):
        try:
            import zstandard as zstd
        except ImportError:
            import zstd  # type: ignore

            def decompress(b: bytes) -> bytes:
                return zstd.decompress(b)
        else:

            def decompress(b: bytes) -> bytes:
                return zstd.ZstdDecompressor().decompress(b)

        logical = decompress(path.read_bytes())
        assert logical[:4] == b"NVSC"
        off = 8

        def read_str(buf, o):
            (n,) = struct.unpack_from("<H", buf, o)
            o += 2
            s = buf[o : o + n].decode()
            return s, o + n

        _, off = read_str(logical, off)
        _, off = read_str(logical, off)
        off += 32 + 8
        (nsec,) = struct.unpack_from("<I", logical, off)
        off += 4
        secs = []
        for _ in range(nsec):
            name, off = read_str(logical, off)
            unc, comp, = struct.unpack_from("<QQ", logical, off)
            off += 16
            off += 32
            (soff,) = struct.unpack_from("<Q", logical, off)
            off += 8
            secs.append((name, unc, comp, soff))
        payload = off
        for name, unc, comp, soff in secs:
            if name != "wetland_edges":
                continue
            raw = decompress(logical[payload + soff : payload + soff + comp])
            (count,) = struct.unpack_from("<I", raw, 0)
            out = []
            o = 4
            for _ in range(count):
                fid, seg, direction, cls = struct.unpack_from("<IHBB", raw, o)
                out.append(
                    {
                        "feature_id": fid,
                        "segment_index": seg,
                        "direction": direction,
                        "class": cls,
                    }
                )
                o += 8
            return out
        self.fail("wetland_edges missing")

    def test_flat_sidecar_not_in_validation_path(self):
        validate = (ROOT / "scripts/mwm/validate-comaps-release.sh").read_text()
        self.assertNotIn("flat-wetland-sidecar.py", validate)
        six = (ROOT / "scripts/mwm/run-six-route-check.sh").read_text()
        self.assertNotIn("flat-wetland-sidecar.py", six)
        self.assertIn("NAVI_WETLAND_SIDECAR_DIR", six)


if __name__ == "__main__":
    unittest.main()
