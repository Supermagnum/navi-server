"""Unit tests for datex_common — no network."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.datex_common.enable import is_truthy, provider_enabled  # noqa: E402
from plugins.datex_common.publish import (  # noqa: E402
    atomic_write_bytes,
    provider_publish_dir,
    write_source_json,
)
from plugins.datex_common.providers_index import rebuild_providers_index  # noqa: E402
from plugins.datex_common.secrets import AuthError, load_basic_credentials  # noqa: E402


class EnableTests(unittest.TestCase):
    def test_truthy(self):
        self.assertFalse(is_truthy(None))
        self.assertFalse(is_truthy("0"))
        self.assertTrue(is_truthy("1"))
        self.assertTrue(is_truthy("yes"))

    def test_provider_enabled_fail_closed(self):
        self.assertFalse(provider_enabled("npra", {}))
        self.assertFalse(provider_enabled("npra", {"NAVI_DATEX_NPRA_ENABLED": "0"}))
        self.assertTrue(provider_enabled("npra", {"NAVI_DATEX_NPRA_ENABLED": "1"}))

    def test_disabled_skips_secret_read(self):
        """Documented contract: callers must gate before secrets; helper is fail-closed."""
        self.assertFalse(provider_enabled("npra", {"NAV_DATEX_PASSWORD": "x"}))


class SecretsTests(unittest.TestCase):
    def test_env_credentials(self):
        creds = load_basic_credentials(
            environ={"NAV_DATEX_USERNAME": "u", "NAV_DATEX_PASSWORD": "p"},
            secrets_file=Path("/nonexistent"),
        )
        self.assertEqual(creds.username, "u")
        self.assertEqual(creds.password, "p")

    def test_missing_fail_closed(self):
        with self.assertRaises(AuthError):
            load_basic_credentials(environ={}, secrets_file=Path("/nonexistent"))


class PublishTests(unittest.TestCase):
    def test_provider_publish_dir(self):
        root = Path("/tmp/pack")
        self.assertEqual(
            provider_publish_dir(root, "npra"),
            root / "published" / "datex" / "npra",
        )
        with self.assertRaises(ValueError):
            provider_publish_dir(root, "../evil")

    def test_atomic_write_and_source_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            pub = Path(tmp) / "published" / "datex" / "npra"
            atomic_write_bytes(pub / "GetSituation.xml", b"<xml/>")
            path = write_source_json(
                pub,
                {"source": "test", "provider_id": "npra"},
                endpoints_ok=["GetSituation"],
            )
            self.assertTrue(path.is_file())
            meta = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(meta["schema"], 1)
            self.assertEqual(meta["endpoints"], ["GetSituation"])
            self.assertEqual((pub / "GetSituation.xml").read_bytes(), b"<xml/>")


class ProvidersIndexTests(unittest.TestCase):
    def test_rebuild_lists_npra_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pub = root / "published" / "datex" / "npra"
            pub.mkdir(parents=True)
            (pub / "source.json").write_text('{"schema":1}\n', encoding="utf-8")
            out = rebuild_providers_index(
                root,
                environ={"NAVI_DATEX_NPRA_ENABLED": "0"},
            )
            data = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], 1)
            self.assertEqual(len(data["providers"]), 1)
            entry = data["providers"][0]
            self.assertEqual(entry["id"], "npra")
            self.assertFalse(entry["enabled"])
            self.assertTrue(entry["has_data"])
            blob = json.dumps(data)
            self.assertNotIn("password", blob.lower())
            self.assertNotIn("NAV_DATEX", blob)


if __name__ == "__main__":
    unittest.main()
