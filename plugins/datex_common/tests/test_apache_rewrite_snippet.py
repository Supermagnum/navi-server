"""Lightweight checks for DATEX Apache rewrite snippet (no live httpd required)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

CONF = Path(__file__).resolve().parents[3] / "http" / "apache-navi-packs.conf"


class ApacheDatexRewriteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = CONF.read_text(encoding="utf-8")

    def test_legacy_endpoint_redirect_present(self):
        self.assertRegex(
            self.text,
            r"RewriteRule\s+\^/datex/\(GetSituation\|GetTravelTimeData\|"
            r"GetMeasuredWeatherData\|GetCCTVSiteTable\)\\\.xml\$\s+"
            r"/datex/npra/\$1\.xml\s+\[R=301,L\]",
        )

    def test_legacy_source_json_redirect_present(self):
        self.assertRegex(
            self.text,
            r"RewriteRule\s+\^/datex/source\\\.json\$\s+/datex/npra/source\.json\s+\[R=301,L\]",
        )

    def test_providers_json_not_redirected_to_npra(self):
        # Must not rewrite providers.json into the npra namespace.
        bad = re.compile(r"RewriteRule[^\n]*providers\.json[^\n]*npra", re.I)
        self.assertIsNone(bad.search(self.text))


if __name__ == "__main__":
    unittest.main()
