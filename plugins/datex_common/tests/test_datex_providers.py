"""Unit tests for generic DATEX provider config, auth, and multi-poll isolation."""

from __future__ import annotations

import io
import json
import logging
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from plugins.datex_common.auth_types import (
    AuthMaterial,
    apply_auth_headers,
    load_auth_material,
    redact_secrets,
)
from plugins.datex_common.fetcher import (
    build_directory_aggregate,
    detect_datex_version,
    list_xml_hrefs_from_html,
    resolve_publication_url,
    validate_datex_xml,
    _maybe_gunzip,
)
from plugins.datex_common.multi_poll import poll_provider
from plugins.datex_common.provider_spec import parse_provider_dict
from plugins.datex_common.secrets import Credentials
import gzip


class TestProviderSpec(unittest.TestCase):
    def test_parse_basic(self):
        spec = parse_provider_dict(
            {
                "id": "no-vegvesen",
                "publish_id": "npra",
                "display_name": "NPRA",
                "country": "NO",
                "attribution": "credit NPRA",
                "licence": "NLOD 2.0",
                "datex_version": "3.1",
                "auth_type": "basic",
                "publications": [
                    {
                        "name": "GetSituation",
                        "url": "https://example.test/GetSituation",
                        "interval_secs": 300,
                    }
                ],
            }
        )
        self.assertEqual(spec.public_id, "npra")
        self.assertEqual(spec.canonical_base, "/datex/npra/")


class TestAuth(unittest.TestCase):
    def test_basic_from_env(self):
        spec = parse_provider_dict(
            {
                "id": "p1",
                "display_name": "P",
                "country": "X",
                "attribution": "a",
                "licence": "l",
                "datex_version": "3.1",
                "auth_type": "basic",
                "publications": [{"name": "f", "url": "https://example.test/f"}],
            }
        )
        auth = load_auth_material(
            spec,
            environ={
                "NAV_DATEX_USERNAME": "fake-user",
                "NAV_DATEX_PASSWORD": "fake-pass",
            },
        )
        self.assertEqual(auth.kind, "basic")
        url, headers = apply_auth_headers(spec, auth, {}, "https://example.test/f")
        self.assertTrue(headers["Authorization"].startswith("Basic "))
        self.assertNotIn("fake-pass", url)

    def test_api_key_header(self):
        spec = parse_provider_dict(
            {
                "id": "tv",
                "display_name": "TV",
                "country": "SE",
                "attribution": "a",
                "licence": "l",
                "datex_version": "3",
                "auth_type": "api-key",
                "api_key_in": "header",
                "api_key_name": "Ocp-Apim-Subscription-Key",
                "publications": [{"name": "f", "url": "https://example.test/f"}],
            }
        )
        auth = AuthMaterial(kind="api-key", token="fake-api-key-value")
        _, headers = apply_auth_headers(spec, auth, {}, "https://example.test/f")
        self.assertEqual(headers["Ocp-Apim-Subscription-Key"], "fake-api-key-value")

    def test_bearer(self):
        spec = parse_provider_dict(
            {
                "id": "b",
                "display_name": "B",
                "country": "X",
                "attribution": "a",
                "licence": "l",
                "datex_version": "3",
                "auth_type": "bearer",
                "publications": [{"name": "f", "url": "https://example.test/f"}],
            }
        )
        auth = load_auth_material(
            spec, environ={"NAV_DATEX_BEARER_TOKEN": "fake-bearer-token"}
        )
        _, headers = apply_auth_headers(spec, auth, {}, "https://example.test/f")
        self.assertEqual(headers["Authorization"], "Bearer fake-bearer-token")

    def test_redact(self):
        auth = AuthMaterial(
            kind="basic", basic=Credentials(username="u", password="super-secret")
        )
        self.assertNotIn("super-secret", redact_secrets("err super-secret here", auth))


class TestDatexDetect(unittest.TestCase):
    def test_v3(self):
        xml = (
            b'<?xml version="1.0"?><ns2:messageContainer '
            b'xmlns:ns2="http://datex2.eu/schema/3/messageContainer"/>'
        )
        self.assertTrue(detect_datex_version(xml).startswith("3"))
        ok, _ = validate_datex_xml(xml, "3.1")
        self.assertTrue(ok)

    def test_v2(self):
        xml = (
            b'<?xml version="1.0"?><d2LogicalModel '
            b'xmlns="http://datex2.eu/schema/2/2_0"/>'
        )
        self.assertTrue(detect_datex_version(xml).startswith("2"))
        ok, _ = validate_datex_xml(xml, "2.3")
        self.assertTrue(ok)

    def test_energy_infra_v3(self):
        xml = b'<?xml version="1.0"?><EnergyInfrastructureTable/>'
        self.assertTrue(detect_datex_version(xml).startswith("3"))
        ok, _ = validate_datex_xml(xml, "3.3")
        self.assertTrue(ok)

    def test_gunzip_magic(self):
        raw = b'<?xml version="1.0"?><d2LogicalModel xmlns="http://datex2.eu/schema/2/2_0"/>'
        gz = gzip.compress(raw)
        self.assertEqual(_maybe_gunzip(gz), raw)
        self.assertEqual(_maybe_gunzip(raw), raw)


class TestUrlDateAndDirectory(unittest.TestCase):
    def test_resolve_yyyymmdd(self):
        spec = parse_provider_dict(
            {
                "id": "bg-ria",
                "display_name": "BG",
                "country": "BG",
                "attribution": "a",
                "licence": "l",
                "datex_version": "2",
                "auth_type": "none",
                "url_date_tz": "Europe/Sofia",
                "publications": [
                    {
                        "name": "d01",
                        "url": "https://example.test/{YYYYMMDD}_danger_d01.xml",
                    }
                ],
            }
        )
        from datetime import datetime
        from zoneinfo import ZoneInfo

        now = datetime(2026, 11, 20, 12, 0, tzinfo=ZoneInfo("UTC")).timestamp()
        url = resolve_publication_url(spec, spec.publications[0], now=now)
        self.assertIn("20261120_danger_d01.xml", url)

    def test_html_index_listing(self):
        html = b"""<html><a href="10.xml">10</a><a href="2.xml">2</a><a href="foo.xml">f</a></html>"""
        urls = list_xml_hrefs_from_html(html, "https://example.test/dir/")
        self.assertEqual(urls[0], "https://example.test/dir/10.xml")
        self.assertIn("https://example.test/dir/2.xml", urls)

    def test_directory_aggregate(self):
        parts = [
            (
                "1.xml",
                b'<?xml version="1.0"?><d2LogicalModel xmlns="http://datex2.eu/schema/2/2_0"/>',
            )
        ]
        blob = build_directory_aggregate(parts, source_url="https://example.test/")
        self.assertIn(b"naviDatexDirectoryAggregate", blob)
        ok, _ = validate_datex_xml(blob, "")
        self.assertTrue(ok)


class TestMockFetch(unittest.TestCase):
    def test_open_provider_and_isolated_auth_failure(self):
        v2 = (
            b'<?xml version="1.0"?><d2LogicalModel '
            b'xmlns="http://datex2.eu/schema/2/2_0"><payloadPublication/></d2LogicalModel>'
        )

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.end_headers()
                self.wfile.write(v2)

            def log_message(self, *_args):
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                good = parse_provider_dict(
                    {
                        "id": "open1",
                        "display_name": "Open",
                        "country": "FI",
                        "attribution": "a",
                        "licence": "l",
                        "datex_version": "2.3",
                        "auth_type": "none",
                        "publications": [
                            {
                                "name": "feed",
                                "url": f"http://127.0.0.1:{port}/open",
                            }
                        ],
                    }
                )
                bad = parse_provider_dict(
                    {
                        "id": "bad1",
                        "display_name": "Bad",
                        "country": "X",
                        "attribution": "a",
                        "licence": "l",
                        "datex_version": "3.1",
                        "auth_type": "basic",
                        "publications": [
                            {
                                "name": "feed",
                                "url": f"http://127.0.0.1:{port}/bad",
                            }
                        ],
                    }
                )

                log_buf = io.StringIO()
                handler = logging.StreamHandler(log_buf)
                log = logging.getLogger("navi.datex")
                log.addHandler(handler)
                log.setLevel(logging.INFO)
                try:
                    code_bad, _ = poll_provider(
                        bad, pack_root=root, jitter=False, force=True
                    )
                    self.assertEqual(code_bad, 2)
                    code_good, results = poll_provider(
                        good, pack_root=root, jitter=False, force=True
                    )
                    self.assertEqual(code_good, 0)
                    self.assertTrue(results)
                    self.assertEqual(results[0].status, 200)
                    published = (
                        root / "published" / "datex" / "open1" / "feed.xml"
                    )
                    self.assertTrue(published.is_file())
                    blob = log_buf.getvalue().lower()
                    self.assertNotIn("fake-pass", blob)
                    self.assertNotIn("password=", blob)
                    # status file exists for both
                    self.assertTrue(
                        (root / "datex_state" / "bad1" / "status.json").is_file()
                    )
                    self.assertTrue(
                        (root / "datex_state" / "open1" / "status.json").is_file()
                    )
                    st = json.loads(
                        (root / "datex_state" / "open1" / "status.json").read_text()
                    )
                    self.assertTrue(st.get("ok"))
                finally:
                    log.removeHandler(handler)
        finally:
            server.shutdown()


if __name__ == "__main__":
    unittest.main()
