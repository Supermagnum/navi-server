"""Unit tests for RadioID mirror — no live network."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.radioid import JOIN_KEY  # noqa: E402
from plugins.radioid.config import load_config  # noqa: E402
from plugins.radioid.mapcache import iter_markers, load_coords_for_countries  # noqa: E402
from plugins.radioid.normalize import (  # noqa: E402
    join_and_normalize,
    normalize_row,
    offset_to_shift_hz,
)
from plugins.radioid.run import run_mirror  # noqa: E402
from plugins.radioid.secrets import AuthError, load_optional_token  # noqa: E402
from plugins.repeaterbook.normalize import frequency_to_hz  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"


def _api_rows():
    return json.loads((FIX / "Norway.api.synthetic.json").read_text())["results"]


def _map_by():
    markers = json.loads((FIX / "Norway.map.synthetic.json").read_text())["markers"]
    return {int(m["locator"]): m for m in markers}


class GateTests(unittest.TestCase):
    def test_permission_gate_no_network(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            env = {
                "NAVI_RADIOID_ENABLED": "1",
                "RADIOID_PERMISSION_CONFIRMED": "0",
                "NAVI_PACK_ROOT": td,
            }
            opener = mock.Mock(side_effect=AssertionError("no network"))
            self.assertEqual(run_mirror(environ=env, opener=opener), 0)
            opener.assert_not_called()


class SecretsTests(unittest.TestCase):
    def test_missing_file_anonymous(self) -> None:
        tok = load_optional_token(environ={}, secrets_file=Path("/nonexistent-radioid"))
        self.assertIsNone(tok.token)

    def test_wrong_permissions_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "radioid.env"
            p.write_text("RADIOID_API_TOKEN=secretvalue12345\n", encoding="utf-8")
            p.chmod(0o644)
            with self.assertRaises(AuthError):
                load_optional_token(environ={}, secrets_file=p)

    def test_load_0600_no_log_leak(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "radioid.env"
            secret = "radioid-secret-token-xyz"
            p.write_text(f"RADIOID_API_TOKEN={secret}\n", encoding="utf-8")
            p.chmod(0o600)
            tok = load_optional_token(environ={}, secrets_file=p)
            self.assertEqual(tok.token, secret)
            self.assertNotIn(secret, repr(tok))


class NormalizeTests(unittest.TestCase):
    def test_freq_and_offset_sign(self) -> None:
        row = _api_rows()[0]
        n = normalize_row(row, map_row=_map_by()[242901])
        self.assertEqual(n["output_hz"], frequency_to_hz("434.88750"))
        self.assertEqual(n["shift_hz"], offset_to_shift_hz("-2.000"))
        self.assertEqual(n["shift_hz"], -2_000_000)
        self.assertEqual(n["dmr_color_code"], 1)
        self.assertFalse(n["position_null"])
        self.assertAlmostEqual(n["lat"], 69.6492, places=4)

    def test_missing_coords_marked(self) -> None:
        row = _api_rows()[2]
        n = normalize_row(row, map_row=_map_by()[242903])
        self.assertTrue(n["position_null"])
        self.assertIsNone(n["lat"])
        self.assertIsNone(n.get("position"))

    def test_off_air_excluded(self) -> None:
        n = normalize_row(_api_rows()[1], map_row=_map_by()[242902])
        self.assertTrue(n["exclude_recommended"])

    def test_talkgroups(self) -> None:
        n = normalize_row(_api_rows()[0], map_row=_map_by()[242901])
        self.assertEqual(n["talkgroups"][0]["timeslot"], 1)

    def test_join_stats(self) -> None:
        records, stats = join_and_normalize(_api_rows(), _map_by())
        self.assertEqual(stats["join_key"], JOIN_KEY)
        self.assertEqual(stats["matched"], 2)  # 901+902 have coords
        self.assertEqual(stats["api_only"], 1)  # 903 null coords
        self.assertEqual(stats["map_only"], 1)  # 242999
        self.assertEqual(len(records), 3)


class MapStreamTests(unittest.TestCase):
    def test_iter_markers_and_filter(self) -> None:
        path = FIX / "Norway.map.synthetic.json"
        markers = list(iter_markers(path))
        self.assertEqual(len(markers), 4)
        by = load_coords_for_countries(path, ["Norway"])
        self.assertIn(242901, by)
        self.assertNotIn(999999, by)


class PublishIsolationTests(unittest.TestCase):
    def test_fixture_publish_and_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pub = root / "published" / "repeaters"
            # First: repeaterbook synthetic via its run
            from plugins.repeaterbook.run import run_mirror as rb_run
            from plugins.repeaterbook.client import parse_export_payload

            rb_fix = ROOT / "plugins" / "repeaterbook" / "tests" / "fixtures"
            rb_rows = parse_export_payload(
                json.loads((rb_fix / "Norway.synthetic.json").read_text())
            )
            env_rb = {
                "NAVI_REPEATERBOOK_DRY_RUN": "1",
                "NAVI_PACK_ROOT": str(root),
                "NAVI_REPEATERBOOK_COUNTRIES": "Norway",
            }
            from plugins.repeaterbook.config import load_config as rb_cfg

            cfg_rb = rb_cfg(env_rb)
            self.assertEqual(
                rb_run(
                    cfg_rb,
                    environ=env_rb,
                    fixture_rows={"Norway": rb_rows},
                    generation="20260101T000000Z",
                    now_unix=1,
                ),
                0,
            )
            # RadioID publish
            env_rid = {
                "NAVI_RADIOID_DRY_RUN": "1",
                "NAVI_PACK_ROOT": str(root),
                "NAVI_RADIOID_COUNTRIES": "Norway",
            }
            cfg = load_config(env_rid)
            rc = run_mirror(
                cfg,
                environ=env_rid,
                fixture_api={"Norway": _api_rows()},
                fixture_map_by_locator=_map_by(),
                generation="20260101T010000Z",
                now_unix=2,
            )
            self.assertEqual(rc, 0)
            cur = json.loads((pub / "current.json").read_text())
            sources = {s["source"]: s for s in cur["sources"]}
            self.assertIn("repeaterbook", sources)
            self.assertIn("radioid", sources)
            self.assertTrue(
                (pub / "repeaterbook" / "no" / "20260101T000000Z" / "manifest.json").is_file()
            )
            self.assertTrue(
                (pub / "radioid" / "no" / "20260101T010000Z" / "manifest.json").is_file()
            )
            # RadioID failure must not remove repeaterbook
            rc2 = run_mirror(
                cfg,
                environ=env_rid,
                fixture_api={"Norway": []},
                fixture_map_by_locator=_map_by(),
                generation="20260101T020000Z",
                now_unix=3,
            )
            self.assertNotEqual(rc2, 0)
            cur2 = json.loads((pub / "current.json").read_text())
            sources2 = {s["source"]: s for s in cur2["sources"]}
            self.assertIn("repeaterbook", sources2)
            self.assertEqual(
                sources2["radioid"]["countries"][0]["generation"], "20260101T010000Z"
            )


if __name__ == "__main__":
    unittest.main()
