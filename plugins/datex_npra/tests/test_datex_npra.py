"""Unit tests for DATEX NPRA plugin — no live vegvesen.no calls."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Allow `python3 -m unittest` from repo root / plugins parent.
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.datex_npra.auth import (  # noqa: E402
    AuthError,
    auth_failure_message,
    load_credentials,
)
from plugins.datex_npra.client import (  # noqa: E402
    FetchResult,
    _ResponseShim,
    fetch_endpoint,
)
from plugins.datex_npra.config import Config, is_enabled, load_config  # noqa: E402
from plugins.datex_npra.poll import poll_once  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


class EnabledGateTests(unittest.TestCase):
    def test_disabled_by_default(self):
        self.assertFalse(is_enabled({}))
        self.assertFalse(is_enabled({"NAVI_DATEX_NPRA_ENABLED": "0"}))
        self.assertFalse(is_enabled({"NAVI_DATEX_NPRA_ENABLED": "false"}))

    def test_enabled_explicit(self):
        self.assertTrue(is_enabled({"NAVI_DATEX_NPRA_ENABLED": "1"}))
        self.assertTrue(is_enabled({"NAVI_DATEX_NPRA_ENABLED": "true"}))

    def test_disabled_short_circuit_zero_network(self):
        """Most important: enabled=false must not open any network."""
        calls = []

        def boom(request, timeout):
            calls.append((str(request.full_url), timeout))
            raise AssertionError("network opener must not be called when disabled")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "NAVI_DATEX_NPRA_ENABLED": "0",
                "NAVI_PACK_ROOT": str(root),
                "NAV_DATEX_USERNAME": "should-not-be-read",
                "NAV_DATEX_PASSWORD": "should-not-be-read",
            }
            # Spy on credential loader — must not run when disabled.
            with mock.patch(
                "plugins.datex_npra.poll.load_credentials",
                side_effect=AssertionError("credentials must not be loaded when disabled"),
            ):
                rc = poll_once(environ=env, opener=boom, jitter=False)
            self.assertEqual(rc, 0)
            self.assertEqual(calls, [])
            self.assertFalse((root / "published" / "datex").exists())


class AuthMessageTests(unittest.TestCase):
    def test_missing_401_403_messages(self):
        self.assertIn("credentials not configured", auth_failure_message("missing"))
        self.assertIn("401", auth_failure_message("401"))
        self.assertIn("403", auth_failure_message("403"))

    def test_env_credentials(self):
        creds = load_credentials(
            environ={"NAV_DATEX_USERNAME": "u", "NAV_DATEX_PASSWORD": "p"},
            secrets_file=Path("/nonexistent"),
        )
        self.assertEqual(creds.username, "u")
        self.assertEqual(creds.password, "p")

    def test_secrets_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secrets.env"
            path.write_text(
                "NAV_DATEX_USERNAME=fileuser\nNAV_DATEX_PASSWORD=filepass\n",
                encoding="utf-8",
            )
            creds = load_credentials(environ={}, secrets_file=path)
            self.assertEqual(creds.username, "fileuser")
            self.assertEqual(creds.password, "filepass")

    def test_missing_credentials_fail_closed(self):
        with self.assertRaises(AuthError) as ctx:
            load_credentials(environ={}, secrets_file=Path("/nonexistent"))
        self.assertEqual(str(ctx.exception), auth_failure_message("missing"))


class ConditionalGetTests(unittest.TestCase):
    def _cfg(self, root: Path) -> Config:
        return load_config(
            {
                "NAVI_DATEX_NPRA_ENABLED": "1",
                "NAVI_DATEX_NPRA_BASE_URL": "https://datex.test.example",
                "NAVI_DATEX_NPRA_ENDPOINTS": "GetSituation",
                "NAVI_DATEX_NPRA_USE_IF_MODIFIED_SINCE": "1",
                "NAVI_DATEX_NPRA_USER_AGENT": "navi-test/0",
            },
            pack_root=root,
        )

    def test_304_not_modified_keeps_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = self._cfg(root)
            cfg.publish_dir.mkdir(parents=True)
            cfg.state_dir.mkdir(parents=True)
            xml_path = cfg.publish_dir / "GetSituation.xml"
            fixture = (FIXTURES / "GetSituation.synthetic.xml").read_bytes()
            xml_path.write_bytes(fixture)
            (cfg.state_dir / "GetSituation.json").write_text(
                json.dumps({"last_modified": "Wed, 01 Jan 2020 00:00:00 GMT"}),
                encoding="utf-8",
            )

            def opener(request, timeout):
                ims = request.get_header("If-modified-since") or request.headers.get(
                    "If-modified-since"
                ) or request.headers.get("If-Modified-Since")
                self.assertEqual(ims, "Wed, 01 Jan 2020 00:00:00 GMT")
                auth = request.get_header("Authorization") or request.headers.get(
                    "Authorization"
                )
                self.assertTrue(auth and auth.startswith("Basic "))
                return _ResponseShim(304, b"", {"Last-Modified": "Wed, 01 Jan 2020 00:00:00 GMT"})

            from plugins.datex_npra.auth import Credentials

            result = fetch_endpoint(
                cfg,
                "GetSituation",
                Credentials("u", "p"),
                opener=opener,
                jitter=False,
            )
            self.assertIsInstance(result, FetchResult)
            self.assertEqual(result.status, 304)
            self.assertTrue(result.not_modified)
            self.assertEqual(xml_path.read_bytes(), fixture)

    def test_200_writes_publish_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = self._cfg(root)
            body = (FIXTURES / "GetSituation.synthetic.xml").read_bytes()

            def opener(request, timeout):
                return _ResponseShim(
                    200,
                    body,
                    {"Last-Modified": "Thu, 02 Jan 2020 00:00:00 GMT"},
                )

            from plugins.datex_npra.auth import Credentials

            result = fetch_endpoint(
                cfg,
                "GetSituation",
                Credentials("u", "p"),
                opener=opener,
                jitter=False,
            )
            self.assertEqual(result.status, 200)
            self.assertFalse(result.not_modified)
            self.assertEqual((cfg.publish_dir / "GetSituation.xml").read_bytes(), body)
            self.assertEqual((cfg.cache_dir / "GetSituation.xml").read_bytes(), body)
            state = json.loads((cfg.state_dir / "GetSituation.json").read_text(encoding="utf-8"))
            self.assertEqual(state["last_modified"], "Thu, 02 Jan 2020 00:00:00 GMT")
            self.assertNotIn("password", json.dumps(state).lower())
            self.assertNotIn("authorization", json.dumps(state).lower())

    def test_401_raises_canonical_message(self):
        import urllib.error

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = self._cfg(root)

            def opener(request, timeout):
                raise urllib.error.HTTPError(
                    url=str(request.full_url),
                    code=401,
                    msg="Unauthorized",
                    hdrs=None,
                    fp=None,
                )

            from plugins.datex_npra.auth import Credentials

            with self.assertRaises(AuthError) as ctx:
                fetch_endpoint(
                    cfg,
                    "GetSituation",
                    Credentials("u", "p"),
                    opener=opener,
                    jitter=False,
                )
            self.assertEqual(str(ctx.exception), auth_failure_message("401"))


class EnabledPollWritesAttribution(unittest.TestCase):
    def test_poll_writes_source_json_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = (FIXTURES / "GetSituation.synthetic.xml").read_bytes()
            env = {
                "NAVI_DATEX_NPRA_ENABLED": "1",
                "NAVI_PACK_ROOT": str(root),
                "NAVI_DATEX_NPRA_BASE_URL": "https://datex.test.example",
                "NAVI_DATEX_NPRA_ENDPOINTS": "GetSituation",
                "NAVI_DATEX_NPRA_USER_AGENT": "navi-test/0",
                "NAV_DATEX_USERNAME": "u",
                "NAV_DATEX_PASSWORD": "p",
            }

            def opener(request, timeout):
                return _ResponseShim(200, body, {})

            rc = poll_once(environ=env, opener=opener, jitter=False)
            self.assertEqual(rc, 0)
            source = json.loads(
                (root / "published" / "datex" / "source.json").read_text(encoding="utf-8")
            )
            blob = json.dumps(source)
            self.assertIn("NPRA", source["source"])
            self.assertNotIn("password", blob.lower())
            self.assertNotIn("NAV_DATEX", blob)
            self.assertNotEqual(source.get("username", None), "u")




class IntervalHoldTests(unittest.TestCase):
    def test_parse_endpoint_intervals_defaults(self):
        cfg = load_config({"NAVI_DATEX_NPRA_ENABLED": "1"}, pack_root=Path("/tmp"))
        self.assertEqual(cfg.interval_for("GetSituation"), 300)
        self.assertEqual(cfg.interval_for("GetMeasuredWeatherData"), 600)
        self.assertEqual(cfg.interval_for("GetCCTVSiteTable"), 43200)

    def test_parse_endpoint_intervals_override(self):
        cfg = load_config(
            {
                "NAVI_DATEX_NPRA_ENABLED": "1",
                "NAVI_DATEX_NPRA_ENDPOINT_INTERVALS": "GetCCTVSiteTable=7200,GetMeasuredWeatherData=900",
            },
            pack_root=Path("/tmp"),
        )
        self.assertEqual(cfg.interval_for("GetCCTVSiteTable"), 7200)
        self.assertEqual(cfg.interval_for("GetMeasuredWeatherData"), 900)
        self.assertEqual(cfg.interval_for("GetSituation"), 300)

    def test_interval_hold_skips_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = load_config(
                {
                    "NAVI_DATEX_NPRA_ENABLED": "1",
                    "NAVI_DATEX_NPRA_ENDPOINTS": "GetSituation",
                    "NAVI_DATEX_NPRA_ENDPOINT_INTERVALS": "GetSituation=600",
                },
                pack_root=root,
            )
            cfg.publish_dir.mkdir(parents=True)
            cfg.state_dir.mkdir(parents=True)
            body = b"<xml>cached</xml>"
            (cfg.publish_dir / "GetSituation.xml").write_bytes(body)
            now = 1_700_000_000.0
            (cfg.state_dir / "GetSituation.json").write_text(
                json.dumps(
                    {
                        "last_status": 200,
                        "fail_count": 0,
                        "last_success_unix": int(now),
                        "next_attempt_unix": int(now) + 600,
                    }
                ),
                encoding="utf-8",
            )

            def boom(request, timeout):
                raise AssertionError("must not open network during interval_hold")

            from plugins.datex_npra.auth import Credentials

            result = fetch_endpoint(
                cfg,
                "GetSituation",
                Credentials("u", "p"),
                opener=boom,
                jitter=False,
                now=now + 10,
            )
            self.assertEqual(result.status, 0)
            self.assertIn("interval_hold", result.error or "")

    def test_success_schedules_next_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = load_config(
                {
                    "NAVI_DATEX_NPRA_ENABLED": "1",
                    "NAVI_DATEX_NPRA_ENDPOINTS": "GetMeasuredWeatherData",
                    "NAVI_DATEX_NPRA_ENDPOINT_INTERVALS": "GetMeasuredWeatherData=600",
                },
                pack_root=root,
            )
            body = b"<xml>wx</xml>"
            now = 1_700_000_000.0

            def opener(request, timeout):
                return _ResponseShim(200, body, {})

            from plugins.datex_npra.auth import Credentials

            result = fetch_endpoint(
                cfg,
                "GetMeasuredWeatherData",
                Credentials("u", "p"),
                opener=opener,
                jitter=False,
                now=now,
            )
            self.assertEqual(result.status, 200)
            state = json.loads((cfg.state_dir / "GetMeasuredWeatherData.json").read_text())
            self.assertEqual(state["next_attempt_unix"], int(now) + 600)

if __name__ == "__main__":
    unittest.main()
