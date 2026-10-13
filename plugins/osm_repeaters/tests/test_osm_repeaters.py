"""Unit tests for OSM repeaters — no live network."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
FIX = Path(__file__).resolve().parent / "fixtures"

from plugins.osm_repeaters.countries import (  # noqa: E402
    country_prefix_from_relpath,
    plan_countries,
    publish_relpath_for_source,
    slug_for_prefix,
)
from plugins.osm_repeaters.extract import (  # noqa: E402
    dedupe_by_osm_id,
    extract_from_opl_pair,
)
from plugins.osm_repeaters.normalize import (  # noqa: E402
    attribution_block,
    classify_osm,
    content_sha256,
    map_designators,
    parse_frequency_field,
    parse_shift_hz,
)
from plugins.osm_repeaters.opl import parse_opl_line, unescape_opl  # noqa: E402
from plugins.osm_repeaters.run import run_extract  # noqa: E402
from plugins.osm_repeaters.config import load_config  # noqa: E402


class OplTests(unittest.TestCase):
    def test_unescape(self) -> None:
        self.assertEqual(unescape_opl("Test%20%FM"), "Test FM")
        self.assertEqual(unescape_opl("145.625%20%MHz"), "145.625 MHz")

    def test_parse_node(self) -> None:
        el = parse_opl_line(
            "n1001 v3 dV t2026-01-01T10:00:00Z "
            "Tcommunication:amateur_radio:repeater=yes Tname=Test%20%FM x10.69 y60.79"
        )
        assert el is not None
        self.assertEqual(el.osm_type, "node")
        self.assertEqual(el.osm_id, 1001)
        self.assertEqual(el.version, 3)
        self.assertEqual(el.tags["name"], "Test FM")


class FreqShiftTests(unittest.TestCase):
    def test_frequency_formats(self) -> None:
        self.assertEqual(parse_frequency_field("145.625 MHz"), 145_625_000)
        self.assertEqual(parse_frequency_field("145.625000"), 145_625_000)
        self.assertEqual(parse_frequency_field("434800000"), 434_800_000)
        self.assertEqual(parse_frequency_field("434.8250;434.825 MHz"), 434_825_000)

    def test_shift_formats(self) -> None:
        self.assertEqual(parse_shift_hz("-0.6 MHz"), -600_000)
        self.assertEqual(parse_shift_hz("-0,6 Mhz"), -600_000)  # decimal comma still parsed
        self.assertEqual(parse_shift_hz("-2 MHz"), -2_000_000)
        self.assertEqual(parse_shift_hz("2 MHz"), 2_000_000)
        self.assertEqual(parse_shift_hz("2000000"), 2_000_000)
        self.assertEqual(parse_shift_hz("-2Mhz;-2 MHz"), -2_000_000)
        self.assertIsNone(parse_shift_hz("150 MHz"))  # > 100 MHz abs rejected


class DesignatorTests(unittest.TestCase):
    def test_map(self) -> None:
        mode, modes, raw = map_designators("11K2F3E;7K60FXE")
        self.assertEqual(mode, "FM")
        self.assertEqual(modes, ["FM", "DMR"])
        self.assertEqual(raw, ["11K2F3E", "7K60FXE"])

    def test_aprs(self) -> None:
        kind, excl = classify_osm(
            mode="APRS", modes=["APRS"], output_hz=144_800_000, designators=["20K0F2D"]
        )
        self.assertEqual(kind, "aprs")
        self.assertTrue(excl)


class ExtractFixtureTests(unittest.TestCase):
    def test_norway_extract(self) -> None:
        recs, nets, st = extract_from_opl_pair(
            FIX / "norway_a.rep.opl", FIX / "norway.net.opl"
        )
        ids = {r["osm_id"] for r in recs}
        self.assertIn(1001, ids)
        self.assertIn(1002, ids)
        self.assertIn(1003, ids)
        self.assertIn(1005, ids)
        self.assertIn(2001, ids)
        self.assertNotIn(1004, ids)  # repeater=no
        fm = next(r for r in recs if r["osm_id"] == 1001)
        self.assertEqual(fm["shift_hz"], -600_000)
        self.assertEqual(fm["ctcss_encode_hz"], 88.5)
        self.assertEqual(fm["name"], "Test FM")
        mm = next(r for r in recs if r["osm_id"] == 1002)
        self.assertEqual(mm["modes"], ["FM", "DMR"])
        aprs = next(r for r in recs if r["osm_id"] == 1003)
        self.assertEqual(aprs["entry_kind"], "aprs")
        self.assertTrue(aprs["exclude_recommended"])
        val = next(r for r in recs if r["osm_id"] == 1005)
        self.assertEqual(val["callsign"], "LA5VAL")
        way = next(r for r in recs if r["osm_id"] == 2001)
        self.assertEqual(way["coord_source"], "osm_way_centroid")
        self.assertFalse(way["position_null"])
        self.assertEqual(len(nets), 1)
        members = {m["id"]: m for m in nets[0]["members"]}
        self.assertTrue(members[1001]["in_dataset"])
        self.assertFalse(members[9999]["in_dataset"])
        self.assertGreaterEqual(st.get("aprs", 0), 1)

    def test_dedupe_higher_version(self) -> None:
        a, _, _ = extract_from_opl_pair(FIX / "norway_a.rep.opl", FIX / "norway.net.opl")
        b, _, _ = extract_from_opl_pair(FIX / "norway_b.rep.opl", FIX / "norway.net.opl")
        merged, dropped = dedupe_by_osm_id(a + b)
        self.assertGreaterEqual(dropped, 1)
        dup = next(r for r in merged if r["osm_id"] == 3001)
        self.assertEqual(dup["osm_version"], 5)


class CountriesTests(unittest.TestCase):
    def test_prefix_and_slug(self) -> None:
        self.assertEqual(
            publish_relpath_for_source("x", "geofabrik:europe/norway/ostlandet"),
            "europe/norway/ostlandet",
        )
        self.assertEqual(
            country_prefix_from_relpath("europe/norway/ostlandet"), "europe/norway"
        )
        self.assertIsNone(country_prefix_from_relpath("europe/dach"))
        self.assertEqual(slug_for_prefix("europe/norway"), "no")

    def test_plan_from_fixture_conf(self) -> None:
        plans = plan_countries(
            conf_paths=[FIX / "regions.conf"],
            extracts_dir=Path("/tmp/missing-extracts"),
            countries_override=None,
        )
        by = {p.slug: p for p in plans}
        self.assertIn("no", by)
        self.assertIn("se", by)
        self.assertEqual(len(by["no"].regions), 2)


class GateAndPublishTests(unittest.TestCase):
    def test_disabled_never_calls_osmium(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = {
                "NAVI_OSM_REPEATERS_ENABLED": "0",
                "NAVI_PACK_ROOT": str(root),
            }
            runner = mock.Mock(side_effect=AssertionError("osmium must not run"))
            rc = run_extract(environ=env, runner=runner)
            self.assertEqual(rc, 0)
            runner.assert_not_called()

    def test_missing_country_does_not_block_other(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pub = root / "published" / "repeaters"
            env = {
                "NAVI_OSM_REPEATERS_ENABLED": "1",
                "NAVI_OSM_REPEATERS_DRY_RUN": "1",
                "NAVI_PACK_ROOT": str(root),
                "NAVI_OSM_REPEATERS_COUNTRIES": "no=europe/norway,se=europe/sweden",
            }
            cfg = load_config(env)
            cfg = cfg.__class__(**{**cfg.__dict__, "publish_root": pub, "work_dir": root / "work"})
            # Fixture only for Sweden — Norway has empty regions + no fixture → FileNotFoundError
            rc = run_extract(
                cfg,
                environ=env,
                fixture_by_slug={
                    "se": {"sweden": (FIX / "sweden.rep.opl", FIX / "sweden.net.opl")},
                },
                generation="20260101T000000Z",
            )
            self.assertEqual(rc, 1)  # partial failure
            self.assertTrue((pub / "osm" / "se").is_dir())
            self.assertFalse((pub / "osm" / "no").exists())
            cur = json.loads((pub / "current.json").read_text(encoding="utf-8"))
            osm = next(s for s in cur["sources"] if s["source"] == "osm")
            self.assertEqual({c["country_slug"] for c in osm["countries"]}, {"se"})

    def test_unchanged_skips_generation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pub = root / "published" / "repeaters"
            env = {
                "NAVI_OSM_REPEATERS_ENABLED": "1",
                "NAVI_OSM_REPEATERS_DRY_RUN": "1",
                "NAVI_PACK_ROOT": str(root),
                "NAVI_OSM_REPEATERS_COUNTRIES": "se=europe/sweden",
            }
            cfg = load_config(env)
            cfg = cfg.__class__(**{**cfg.__dict__, "publish_root": pub, "work_dir": root / "work"})
            fix = {"se": {"sweden": (FIX / "sweden.rep.opl", FIX / "sweden.net.opl")}}
            self.assertEqual(
                run_extract(cfg, environ=env, fixture_by_slug=fix, generation="20260101T000000Z"),
                0,
            )
            gen1 = list((pub / "osm" / "se").iterdir())
            self.assertEqual(len(gen1), 1)
            self.assertEqual(
                run_extract(cfg, environ=env, fixture_by_slug=fix, generation="20260101T010000Z"),
                0,
            )
            gen2 = list((pub / "osm" / "se").iterdir())
            self.assertEqual(len(gen2), 1)
            self.assertEqual(gen2[0].name, "20260101T000000Z")

    def test_full_fixture_publish_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pub = root / "published" / "repeaters"
            env = {
                "NAVI_OSM_REPEATERS_ENABLED": "1",
                "NAVI_OSM_REPEATERS_DRY_RUN": "1",
                "NAVI_PACK_ROOT": str(root),
                "NAVI_OSM_REPEATERS_COUNTRIES": "no=europe/norway,se=europe/sweden",
            }
            cfg = load_config(env)
            cfg = cfg.__class__(**{**cfg.__dict__, "publish_root": pub, "work_dir": root / "work"})
            fix = {
                "no": {
                    "norway_ostlandet": (FIX / "norway_a.rep.opl", FIX / "norway.net.opl"),
                    "norway_vestlandet": (FIX / "norway_b.rep.opl", FIX / "norway.net.opl"),
                },
                "se": {"sweden": (FIX / "sweden.rep.opl", FIX / "sweden.net.opl")},
            }
            rc = run_extract(cfg, environ=env, fixture_by_slug=fix, generation="20260101T000000Z")
            self.assertEqual(rc, 0)
            cur = json.loads((pub / "current.json").read_text(encoding="utf-8"))
            osm = next(s for s in cur["sources"] if s["source"] == "osm")
            self.assertEqual(osm["attribution"]["text"], "© OpenStreetMap contributors")
            self.assertIn("ODbL", osm.get("licence") or "")
            slugs = {c["country_slug"] for c in osm["countries"]}
            self.assertEqual(slugs, {"no", "se"})
            man = json.loads(
                (pub / "osm" / "no" / "20260101T000000Z" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(man["attribution"]["licence"], "ODbL-1.0")
            self.assertIn("content_sha256", man)
            # dedupe: version 5 of 3001
            import subprocess

            raw = subprocess.check_output(
                [
                    "zstd",
                    "-d",
                    "-c",
                    str(pub / "osm" / "no" / "20260101T000000Z" / "repeaters.json.zst"),
                ]
            )
            env_doc = json.loads(raw)
            dup = next(r for r in env_doc["repeaters"] if r["osm_id"] == 3001)
            self.assertEqual(dup["osm_version"], 5)
            self.assertTrue(any(n["relation_id"] == 9001 for n in env_doc["networks"]))


class AttrTests(unittest.TestCase):
    def test_attribution_block(self) -> None:
        a = attribution_block()
        self.assertEqual(a["licence"], "ODbL-1.0")
        self.assertIn("openstreetmap.org", a["url"])


if __name__ == "__main__":
    unittest.main()
