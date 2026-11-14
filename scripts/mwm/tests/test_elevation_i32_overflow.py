#!/usr/bin/env python3
"""Elevation absolute dm must round-trip above the old i16 cap (3276.7 m)."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SIDECAR_BIN = ROOT / "target" / "release" / "navi-mwm-sidecar"
LIB = Path(__file__).resolve().parents[1] / "lib"


class TestElevationI32Overflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not SIDECAR_BIN.is_file():
            subprocess.check_call(
                [
                    "cargo",
                    "build",
                    "-p",
                    "navi-mwm-sidecar",
                    "--release",
                    "--manifest-path",
                    str(ROOT / "Cargo.toml"),
                ]
            )

    def test_bake_jsonl_high_altitudes_roundtrip(self) -> None:
        # metres → dm: -500, 0, 3276.7, 3276.8, 5300, 8848
        want = [-5000, 0, 32767, 32768, 53000, 88480]
        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            elev = tdir / "elev.jsonl"
            feat = {
                "feature_id": 1,
                "point_count": len(want),
                "points": [
                    {"point_index": i, "altitude_dm": dm} for i, dm in enumerate(want)
                ],
            }
            elev.write_text(json.dumps(feat) + "\n")
            wet = tdir / "wet.jsonl"
            wet.write_text("")
            attr = tdir / "attr.json"
            attr.write_text("{}")
            # Minimal fake MWM file for fingerprint only.
            mwm = tdir / "X.mwm"
            mwm.write_bytes(b"MWMFAKE" + b"\0" * 64)
            out = tdir / "X.navi-sidecar.v1.zst"
            subprocess.check_call(
                [
                    str(SIDECAR_BIN),
                    "bake",
                    "--mwm",
                    str(mwm),
                    "--wetland-jsonl",
                    str(wet),
                    "--elevation-jsonl",
                    str(elev),
                    "--attribution",
                    str(attr),
                    "--generator-tag",
                    "test",
                    "--generator-sha",
                    "test",
                    "--ambiguous-threshold",
                    "0",
                    "--out",
                    str(out),
                ]
            )
            # Inspect via Python parse of body through the binary's read path:
            # use `inspect` then also re-decode elevation with a tiny helper.
            info = json.loads(
                subprocess.check_output(
                    [str(SIDECAR_BIN), "inspect", "--sidecar", str(out)],
                    text=True,
                )
            )
            self.assertEqual(info["elevation_features"], 1)
            self.assertEqual(info["elevation_points"], len(want))

            # Decode via geometry_cache-style absolute values is not enough —
            # call the Rust round-trip by baking again from a dump is heavy.
            # Instead parse with navi_mwm via a one-shot Python binding-less
            # check: re-read using the same bake tool's section compare by
            # dumping JSONL through a small Rust-free decoder.
            import sys

            sys.path.insert(0, str(LIB))
            import zstandard as zstd
            import struct

            raw = zstd.ZstdDecompressor().decompress(out.read_bytes())
            # Walk header to elevation section (same layout as audit script).
            off = 8  # magic + format_version
            for _ in range(2):
                n = struct.unpack_from("<H", raw, off)[0]
                off += 2 + n
            off += 32 + 8  # sha + size
            scount = struct.unpack_from("<I", raw, off)[0]
            off += 4
            sections = []
            for _ in range(scount):
                n = struct.unpack_from("<H", raw, off)[0]
                off += 2
                name = raw[off : off + n].decode()
                off += n
                uncomp, comp = struct.unpack_from("<QQ", raw, off)
                off += 16
                off += 32  # sha
                soff = struct.unpack_from("<Q", raw, off)[0]
                off += 8
                sections.append((name, uncomp, comp, soff))
            payload = raw[off:]
            body = None
            for name, uncomp, comp, soff in sections:
                if name == "elevation_overrides":
                    body = zstd.ZstdDecompressor().decompress(
                        payload[soff : soff + comp]
                    )
                    break
            assert body is not None
            magic = struct.unpack_from("<I", body, 0)[0]
            self.assertEqual(magic, 0x32304C45)  # EL02
            count = struct.unpack_from("<I", body, 4)[0]
            self.assertEqual(count, 1)
            o = 8
            _fid, _pc, n = struct.unpack_from("<IHH", body, o)
            o += 8
            prev = 0
            got = []
            for i in range(n):
                idx_d, zz = struct.unpack_from("<HI", body, o)
                o += 6
                # zigzag i32
                delta = (zz >> 1) ^ (-(zz & 1))
                if delta >= 2**31:
                    delta -= 2**32
                alt = prev + delta
                got.append(alt)
                prev = alt
            self.assertEqual(got, want)

    def test_geometry_cache_stores_i32_absolutes(self) -> None:
        import sys

        sys.path.insert(0, str(LIB))
        from geometry_cache import GeometryResultCache, RoadCacheEntry, policy_epoch

        with tempfile.TemporaryDirectory() as td:
            gc = GeometryResultCache(Path(td), epoch=policy_epoch(zoom=12))
            gc.open_leaf("High")
            samples = [-5000, 0, 32767, 32768, 53000, 88480]
            gh = b"\xab" * 16
            gc.put_road(RoadCacheEntry(geom_hash=gh, n_points=len(samples), samples_dm=samples))
            gc.flush_leaf()
            gc.close()
            gc2 = GeometryResultCache(Path(td), epoch=policy_epoch(zoom=12))
            gc2.open_leaf("High")
            hit = gc2.get_road(gh)
            self.assertIsNotNone(hit)
            assert hit is not None
            self.assertEqual(hit.samples_dm, samples)
            gc2.close()


if __name__ == "__main__":
    unittest.main()
