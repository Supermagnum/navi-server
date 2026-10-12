"""Unit tests for RepeaterBook mirror — no live network."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.repeaterbook.client import (  # noqa: E402
    FetchOutcome,
    _ResponseShim,
    fetch_country,
    parse_export_payload,
)
from plugins.repeaterbook.config import (  # noqa: E402
    build_user_agent,
    is_enabled,
    load_config,
    permission_confirmed,
)
from plugins.repeaterbook.normalize import (  # noqa: E402
    frequency_to_hz,
    normalize_row,
    parse_decimal,
)
from plugins.repeaterbook.publish import (  # noqa: E402
    PublishError,
    validate_count,
)
from plugins.repeaterbook.run import run_mirror  # noqa: E402
from plugins.repeaterbook.secrets import (  # noqa: E402
    TOKEN_REDACT,
    AuthError,
    load_app_token,
    redact_secrets,
    validate_app_token,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SYNTH = FIXTURES / "Norway.synthetic.json"


def _synth_rows() -> List[dict]:
    return parse_export_payload(json.loads(SYNTH.read_text(encoding="utf-8")))


class GateTests(unittest.TestCase):
    def test_disabled_no_network(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = {
                "NAVI_REPEATERBOOK_ENABLED": "0",
                "REPEATERBOOK_PERMISSION_CONFIRMED": "1",
                "NAVI_PACK_ROOT": str(root),
                "REPEATERBOOK_APP_TOKEN": "app_testtokenvalue01",
            }
            opener = mock.Mock(side_effect=AssertionError("network must not be called"))
            rc = run_mirror(environ=env, opener=opener)
            self.assertEqual(rc, 0)
            opener.assert_not_called()
            self.assertFalse((root / "published" / "repeaters").exists())

    def test_permission_not_confirmed_no_network(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = {
                "NAVI_REPEATERBOOK_ENABLED": "1",
                "REPEATERBOOK_PERMISSION_CONFIRMED": "0",
                "NAVI_PACK_ROOT": str(root),
                "REPEATERBOOK_APP_TOKEN": "app_testtokenvalue01",
            }
            opener = mock.Mock(side_effect=AssertionError("network must not be called"))
            rc = run_mirror(environ=env, opener=opener)
            self.assertEqual(rc, 0)
            opener.assert_not_called()


class SecretsTests(unittest.TestCase):
    def test_reject_rbuapp(self) -> None:
        with self.assertRaises(AuthError):
            validate_app_token("rbuapp_userboundtokenvalue")

    def test_accept_app_token(self) -> None:
        self.assertEqual(validate_app_token("app_serverTokenValue01"), "app_serverTokenValue01")

    def test_redact(self) -> None:
        tok = "app_supersecretvalue99"
        msg = redact_secrets(f"failed with {tok} in body", tok)
        self.assertNotIn(tok, msg)
        self.assertIn(TOKEN_REDACT, msg)

    def test_load_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "repeaterbook.env"
            p.write_text("REPEATERBOOK_APP_TOKEN=app_filetokenvalue01\n", encoding="utf-8")
            p.chmod(0o600)
            t = load_app_token(environ={}, secrets_file=p)
            self.assertEqual(t.token, "app_filetokenvalue01")
            self.assertIn(TOKEN_REDACT, repr(t))

    def test_wrong_permissions_refused(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "repeaterbook.env"
            p.write_text("REPEATERBOOK_APP_TOKEN=app_filetokenvalue01\n", encoding="utf-8")
            p.chmod(0o644)
            with self.assertRaises(AuthError):
                load_app_token(environ={}, secrets_file=p)


class NormalizeTests(unittest.TestCase):
    def test_decimal_comma_lat_lon_freq(self) -> None:
        row = _synth_rows()[0]
        n = normalize_row(row)
        self.assertAlmostEqual(n["lat"], 59.9139, places=4)
        self.assertAlmostEqual(n["lon"], 10.7522, places=4)
        self.assertEqual(n["output_hz"], 145_650_000)
        self.assertEqual(n["shift_hz"], 145_050_000 - 145_650_000)

    def test_missing_fields_stay_missing(self) -> None:
        n = normalize_row({"Callsign": "LA0X", "Frequency": "145.5"})
        self.assertEqual(n["callsign"], "LA0X")
        self.assertIsNone(n["lat"])
        self.assertIsNone(n["lon"])
        self.assertNotIn("dmr_color_code", n)

    def test_dmr_color_code(self) -> None:
        n = normalize_row(_synth_rows()[1])
        self.assertEqual(n["dmr_color_code"], 1)
        self.assertEqual(n["mode"], "DMR")

    def test_ctcss_vs_dcs(self) -> None:
        fm = normalize_row(_synth_rows()[0])
        self.assertEqual(fm["ctcss_encode_hz"], 123.0)
        self.assertIsNone(fm.get("dcs_encode"))
        dmr = normalize_row(_synth_rows()[1])
        self.assertEqual(dmr["dcs_encode"], "D023N")
        self.assertIsNone(dmr.get("ctcss_encode_hz"))

    def test_shift_sign(self) -> None:
        # output 145.65 in 145.05 → negative shift
        self.assertEqual(normalize_row(_synth_rows()[0])["shift_hz"], -600_000)

    def test_aprs_and_simplex_flagged(self) -> None:
        aprs = normalize_row(_synth_rows()[2])
        self.assertEqual(aprs["entry_kind"], "aprs")
        self.assertTrue(aprs["exclude_recommended"])
        smp = normalize_row(_synth_rows()[3])
        self.assertEqual(smp["entry_kind"], "simplex")
        self.assertTrue(smp["exclude_recommended"])

    def test_parse_decimal_comma(self) -> None:
        self.assertEqual(parse_decimal("1,5"), 1.5)
        self.assertEqual(frequency_to_hz("145,650"), 145_650_000)


class ClientBackoffTests(unittest.TestCase):
    def test_429_honours_retry_after_then_succeeds(self) -> None:
        cfg = load_config(
            {
                "NAVI_PACK_ROOT": "/tmp/rb-test",
                "NAVI_REPEATERBOOK_MAX_RETRIES": "3",
                "NAVI_REPEATERBOOK_APP_NAME": "NaviTest",
                "NAVI_REPEATERBOOK_APP_VERSION": "0.0",
                "NAVI_REPEATERBOOK_CONTACT": "test@example.com",
            }
        )
        body = json.dumps({"results": [{"Callsign": "LA1", "Frequency": "145.5"}]}).encode()
        calls = {"n": 0}
        sleeps: List[float] = []

        def opener(req, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                return _ResponseShim(429, b"{}", {"Retry-After": "1.5"})
            return _ResponseShim(200, body, {"Content-Type": "application/json"})

        from plugins.repeaterbook.secrets import AppToken

        out = fetch_country(
            cfg,
            "Norway",
            AppToken("app_testtokenvalue01"),
            opener=opener,
            sleeper=lambda s: sleeps.append(s),
            rng=__import__("random").Random(0),
        )
        self.assertEqual(out.status, 200)
        self.assertEqual(len(out.rows), 1)
        self.assertEqual(sleeps[0], 1.5)
        self.assertEqual(calls["n"], 2)

    def test_429_exhaustion_keeps_empty(self) -> None:
        cfg = load_config(
            {
                "NAVI_PACK_ROOT": "/tmp/rb-test",
                "NAVI_REPEATERBOOK_MAX_RETRIES": "1",
            }
        )

        def opener(req, timeout):
            return _ResponseShim(429, b"{}", {"Retry-After": "0"})

        from plugins.repeaterbook.secrets import AppToken

        out = fetch_country(
            cfg,
            "Norway",
            AppToken("app_testtokenvalue01"),
            opener=opener,
            sleeper=lambda _s: None,
        )
        self.assertEqual(out.error, "rate_limited")
        self.assertEqual(out.rows, [])


class PublishGuardTests(unittest.TestCase):
    def test_empty_refused(self) -> None:
        with self.assertRaises(PublishError):
            validate_count(0, 10, ratio_min=0.25, ratio_max=4.0)

    def test_outlier_refused(self) -> None:
        with self.assertRaises(PublishError):
            validate_count(1, 100, ratio_min=0.25, ratio_max=4.0)

    def test_failed_fetch_keeps_last_good(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pub = root / "published" / "repeaters"
            env = {
                "NAVI_REPEATERBOOK_DRY_RUN": "1",
                "NAVI_REPEATERBOOK_ENABLED": "1",
                "REPEATERBOOK_PERMISSION_CONFIRMED": "1",
                "NAVI_PACK_ROOT": str(root),
                "NAVI_REPEATERBOOK_COUNTRIES": "Norway",
            }
            cfg = load_config(env)
            # First success
            rc = run_mirror(
                cfg,
                environ=env,
                fixture_rows={"Norway": _synth_rows()},
                generation="20260101T000000Z",
                now_unix=1,
            )
            self.assertEqual(rc, 0)
            cur1 = json.loads((pub / "current.json").read_text(encoding="utf-8"))
            src = {s["source"]: s for s in cur1["sources"]}["repeaterbook"]
            self.assertEqual(src["countries"][0]["record_count"], 4)
            # Second run fails (empty) — must not clobber
            rc2 = run_mirror(
                cfg,
                environ=env,
                fixture_rows={"Norway": []},
                generation="20260108T000000Z",
                now_unix=2,
            )
            self.assertNotEqual(rc2, 0)
            cur2 = json.loads((pub / "current.json").read_text(encoding="utf-8"))
            src = {s["source"]: s for s in cur2["sources"]}["repeaterbook"]
            self.assertEqual(src["countries"][0]["generation"], "20260101T000000Z")
            self.assertTrue((pub / "repeaterbook" / "no" / "20260101T000000Z" / "repeaters.json.zst").is_file())
            self.assertFalse((pub / "repeaterbook" / "no" / "20260108T000000Z").exists())

    def test_attribution_in_current_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = {
                "NAVI_REPEATERBOOK_DRY_RUN": "1",
                "NAVI_PACK_ROOT": str(root),
                "NAVI_REPEATERBOOK_COUNTRIES": "Norway",
            }
            cfg = load_config(env)
            run_mirror(
                cfg,
                environ=env,
                fixture_rows={"Norway": _synth_rows()},
                generation="20260101T000000Z",
                now_unix=1,
            )
            cur = json.loads(
                (root / "published" / "repeaters" / "current.json").read_text(
                    encoding="utf-8"
                )
            )
            src = {s["source"]: s for s in cur["sources"]}["repeaterbook"]
            self.assertEqual(
                src["attribution"]["text"], "Data courtesy of RepeaterBook.com"
            )
            self.assertEqual(
                src["attribution"]["url"], "https://www.repeaterbook.com/"
            )
            man = json.loads(
                (
                    root
                    / "published"
                    / "repeaters"
                    / "repeaterbook"
                    / "no"
                    / "20260101T000000Z"
                    / "manifest.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                man["attribution"]["text"], "Data courtesy of RepeaterBook.com"
            )

    def test_token_never_in_published_files(self) -> None:
        tok = "app_mustNeverAppearInPublish99"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = {
                "NAVI_REPEATERBOOK_DRY_RUN": "1",
                "NAVI_PACK_ROOT": str(root),
                "REPEATERBOOK_APP_TOKEN": tok,
                "NAVI_REPEATERBOOK_COUNTRIES": "Norway",
            }
            cfg = load_config(env)
            run_mirror(
                cfg,
                environ=env,
                fixture_rows={"Norway": _synth_rows()},
                generation="20260101T000000Z",
                now_unix=1,
            )
            for path in (root / "published" / "repeaters").rglob("*"):
                if path.is_file():
                    data = path.read_bytes()
                    self.assertNotIn(tok.encode(), data, msg=str(path))


class UserAgentConfigTests(unittest.TestCase):
    def test_ua_from_config_parts(self) -> None:
        ua = build_user_agent("NaviServerRB", "1.2", "ops@example.com")
        self.assertEqual(
            ua,
            "NaviServerRB/1.2 (+https://github.com/Supermagnum/navi-server; ops@example.com)",
        )

    def test_ua_override(self) -> None:
        cfg = load_config(
            {
                "NAVI_PACK_ROOT": "/tmp",
                "NAVI_REPEATERBOOK_USER_AGENT": "ExactApproved/9.9 (+https://x; a@b.c)",
            }
        )
        self.assertEqual(cfg.user_agent, "ExactApproved/9.9 (+https://x; a@b.c)")


class EnabledHelpers(unittest.TestCase):
    def test_helpers(self) -> None:
        self.assertFalse(is_enabled({}))
        self.assertTrue(is_enabled({"NAVI_REPEATERBOOK_ENABLED": "1"}))
        self.assertFalse(permission_confirmed({}))
        self.assertTrue(permission_confirmed({"REPEATERBOOK_PERMISSION_CONFIRMED": "yes"}))


if __name__ == "__main__":
    unittest.main()
