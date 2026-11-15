"""Allow-list, negative paths, and checksum verify for migrate_mwm."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))

from migrate_mwm import (  # noqa: E402
    MigrateMwmError,
    assert_not_forbidden,
    attach_checksums_from_release,
    build_plan,
    count_forbidden_in_plan,
    resolve_under,
    verify_copied_file,
)


def _write(p: Path, data: bytes = b"x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


class TestMigrateMwm(unittest.TestCase):
    def _src_tree(self, td: Path) -> Path:
        root = td / "navi-server"
        mwm = root / "data/published/mwm"
        _write(mwm / ".navi-mwm-owned", b"owned-by=navi-mwm-pipeline\n")
        ver = mwm / "261105"
        side = b"sidecar-bytes"
        _write(ver / "Norway_Oslo.navi-sidecar.v1.zst", side)
        digest = hashlib.sha256(side).hexdigest()
        _write(ver / "checksums.sha256", f"{digest}  Norway_Oslo.navi-sidecar.v1.zst\n".encode())
        _write(ver / "catalog.json", b'{"version":"261105"}')
        _write(mwm / "current.json", b'{"version":"261105","schema":1}')
        _write(root / "config/mwm/measured-peaks.json", b"{}")
        # Poison: .rkyv material that must never be planned
        _write(root / "data/published/current.json", b'{"generation":"rkyv"}')
        _write(root / "data/published/packs/europe/x/g/a.rkyv", b"pack")
        return root

    def test_plan_allow_list_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._src_tree(Path(td))
            plan = build_plan(root, with_config=True)
            self.assertFalse(plan.errors)
            rels = [f.rel.as_posix() for f in plan.files]
            self.assertIn("data/published/mwm/current.json", rels)
            self.assertIn("data/published/mwm/.navi-mwm-owned", rels)
            self.assertTrue(any(r.endswith("Norway_Oslo.navi-sidecar.v1.zst") for r in rels))
            self.assertTrue(any(r.startswith("config/mwm/") for r in rels))
            self.assertNotIn("data/published/current.json", rels)
            self.assertFalse(any("/packs/" in r or r.endswith(".rkyv") for r in rels))
            self.assertEqual(count_forbidden_in_plan(plan), 0)
            # current.json last
            self.assertEqual(plan.files[-1].rel.as_posix(), "data/published/mwm/current.json")

    def test_checksum_attach_and_verify(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._src_tree(Path(td))
            plan = build_plan(root, with_config=False)
            attach_checksums_from_release(plan, root)
            side = next(f for f in plan.files if f.rel.name.endswith(".zst"))
            self.assertEqual(len(side.sha256 or ""), 64)
            verify_copied_file(side.src, side.sha256 or "")
            with self.assertRaises(MigrateMwmError):
                verify_copied_file(side.src, "0" * 64)

    def test_resolve_under_rejects_escape(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "root"
            root.mkdir()
            with self.assertRaises(MigrateMwmError):
                resolve_under(root, Path("../outside"))

    def test_forbidden_rkyv_paths(self):
        with self.assertRaises(MigrateMwmError):
            assert_not_forbidden("data/published/current.json")
        with self.assertRaises(MigrateMwmError):
            assert_not_forbidden("data/published/packs/eu/x/a.rkyv")
        assert_not_forbidden("data/published/mwm/current.json")

    def test_missing_marker_errors(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "navi-server"
            (root / "data/published/mwm").mkdir(parents=True)
            plan = build_plan(root)
            self.assertTrue(plan.errors)

    def test_geometry_cache_optional(self):
        with tempfile.TemporaryDirectory() as td:
            root = self._src_tree(Path(td))
            gc = root / "data/elevation/geometry-hash-cache"
            _write(gc / "leaves/Foo.ghash", b"hash")
            plan = build_plan(root, with_geometry_cache=True, with_config=False)
            rels = [f.rel.as_posix() for f in plan.files]
            self.assertTrue(any("geometry-hash-cache" in r for r in rels))


if __name__ == "__main__":
    unittest.main()
