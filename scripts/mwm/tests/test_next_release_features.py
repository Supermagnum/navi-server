"""Tests for World exclusion, cache cap, geometry-hash cache, and policy v2."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(ROOT))

from elevation_policy import (  # noqa: E402
    classify_mapterhorn_leaf,
    leaf_below_60n,
    threshold_m_for_leaf,
)
from geometry_cache import (  # noqa: E402
    GeometryResultCache,
    RoadCacheEntry,
    overrides_from_samples,
    policy_epoch,
    road_geometry_hash,
)
from leaf_scheduler import (  # noqa: E402
    estimate_elevation_rss_gib,
    pending_leaves,
    stage_aware_process_concurrency,
)
from non_routable import (  # noqa: E402
    NON_ROUTABLE_LEAVES,
    is_non_routable_leaf,
    non_routable_catalog_entry,
    routable_leaves,
)


def _load_elev():
    path = ROOT / "build-elevation-overrides.py"
    spec = importlib.util.spec_from_file_location("elev_mod", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _make_terrarium_webp(elev_m: float) -> bytes:
    from PIL import Image
    import io

    # Encode a constant Terrarium elevation into a tiny RGB webp.
    # elev = (R * 256 + G + B / 256) - 32768
    v = elev_m + 32768.0
    r = int(v // 256)
    g = int(v % 256)
    b = int(round((v - int(v)) * 256))
    img = Image.new("RGB", (16, 16), (r, g, b))
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


class TestWorldExclusion(unittest.TestCase):
    def test_non_routable_set(self):
        self.assertEqual(NON_ROUTABLE_LEAVES, frozenset({"World", "WorldCoasts"}))
        self.assertTrue(is_non_routable_leaf("World"))
        self.assertFalse(is_non_routable_leaf("Norway_Hedmark"))
        self.assertEqual(
            routable_leaves(["World", "Norway_Hedmark", "WorldCoasts"]),
            ["Norway_Hedmark"],
        )

    def test_pending_skips_non_routable(self):
        pending = pending_leaves(
            ["World", "Norway_Hedmark", "WorldCoasts", "Sweden_Stockholm"],
            {"leaves": {}},
        )
        self.assertEqual(pending, ["Norway_Hedmark", "Sweden_Stockholm"])

    def test_catalog_entry_marks_no_sidecar(self):
        e = non_routable_catalog_entry("World", {"size": 10, "sha1_base64": "x"})
        self.assertTrue(e["no_sidecar"])
        self.assertIsNone(e["sidecar"])
        self.assertIsNone(e["sidecar_url"])


class TestCacheCapEnforcement(unittest.TestCase):
    def test_write_under_cap_with_parallel_workers(self):
        elev = _load_elev()
        with tempfile.TemporaryDirectory() as td:
            cdir = Path(td)
            elev.init_cache_size(cdir, force_scan=True)
            # Tiny cap: ~3 KiB. Each tile ~200+ bytes.
            cap = 3 * 1024
            webp = _make_terrarium_webp(100.0)
            self.assertGreater(len(webp), 50)

            errors: list[str] = []

            def worker(n: int) -> None:
                try:
                    for i in range(20):
                        path = cdir / "12" / str(n) / f"{i}.webp"
                        elev.write_webp_under_cap(cdir, path, webp, cap)
                except Exception as e:  # noqa: BLE001
                    errors.append(str(e))

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            tracked = elev.read_cache_size(cdir)
            self.assertIsNotNone(tracked)
            self.assertLessEqual(tracked, cap)
            # Disk reality must also stay at/under cap (allow tiny race slack of 0).
            on_disk = sum(p.stat().st_size for p in cdir.rglob("*.webp"))
            self.assertLessEqual(on_disk, cap)

    def test_prefetch_respects_small_cap(self):
        elev = _load_elev()
        webp = _make_terrarium_webp(42.0)
        with tempfile.TemporaryDirectory() as td:
            cdir = Path(td)
            elev.init_cache_size(cdir, force_scan=True)
            cap = 8 * 1024
            cache = elev.TileCache(
                cdir, max_bytes=cap, zoom=12, min_zoom=12, fetch_concurrency=4
            )
            try:
                with mock.patch.object(cache._session, "get_tile", return_value=(200, webp)):
                    cache.prefetch_tiles([(12, 0, i) for i in range(40)])
            finally:
                cache.close()
            tracked = elev.read_cache_size(cdir)
            self.assertIsNotNone(tracked)
            self.assertLessEqual(tracked, cap)


class TestGeometryCache(unittest.TestCase):
    def test_hash_and_epoch_and_fid_remap(self):
        pts = [
            {"lat": 61.05001, "lon": 11.47002, "alt_m": 123.4},
            {"lat": 61.05002, "lon": 11.47003, "alt_m": 124.0},
        ]
        h1 = road_geometry_hash(pts)
        h2 = road_geometry_hash(
            [
                {"lat": 61.050014, "lon": 11.470024, "alt_m": 123.4},
                {"lat": 61.050024, "lon": 11.470034, "alt_m": 124.0},
            ]
        )
        self.assertEqual(h1, h2)  # within 1e-5 quantum
        self.assertEqual(len(h1), 16)
        ep1 = policy_epoch(zoom=12)
        ep2 = policy_epoch(zoom=13)
        self.assertNotEqual(ep1, ep2)
        # Threshold change must NOT change epoch.
        self.assertIn("z=12", ep1)
        self.assertNotIn("threshold", ep1)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "gcache"
            gc = GeometryResultCache(root, epoch=ep1)
            gc.open_leaf("Norway_Hedmark")
            samples = [1234, 1250]
            gc.put_road(
                RoadCacheEntry(
                    geom_hash=h1,
                    n_points=2,
                    samples_dm=samples,
                    wetland=[{"segment_index": 0, "direction": 0, "class": 1}],
                    boardwalk=[{"segment_index": 0, "direction": 1, "flag": 1}],
                )
            )
            gc.flush_leaf()
            gc.close()
            gc2 = GeometryResultCache(root, epoch=ep1)
            gc2.open_leaf("Norway_Hedmark")
            hit = gc2.get_road(h1)
            self.assertIsNotNone(hit)
            assert hit is not None
            # Remap onto a new feature_id at bake time: samples stay, FID is external.
            ovr_2m = overrides_from_samples(hit.samples_dm, [123.4, 124.0], 2.0)
            ovr_5m = overrides_from_samples(hit.samples_dm, [123.4, 124.0], 5.0)
            # samples_dm 1234 → 123.4 m; 1250 → 125.0 m. deltas 0 and 1.0 → empty at 2m and 5m
            self.assertEqual(ovr_2m, [])
            self.assertEqual(ovr_5m, [])
            # Absolute replay at different threshold with larger delta:
            ovr = overrides_from_samples([1234, 1300], [123.4, 124.0], 2.0)
            self.assertEqual(ovr, [{"point_index": 1, "altitude_dm": 1300}])
            ovr5 = overrides_from_samples([1234, 1300], [123.4, 124.0], 5.0)
            self.assertEqual(ovr5, [{"point_index": 1, "altitude_dm": 1300}])
            ovr10 = overrides_from_samples([1234, 1300], [123.4, 124.0], 10.0)
            self.assertEqual(ovr10, [])
            self.assertEqual(hit.wetland[0]["class"], 1)
            # High absolute dm (above i16) must persist in the leaf file.
            hi = RoadCacheEntry(
                geom_hash=b"\x11" * 16,
                n_points=6,
                samples_dm=[-5000, 0, 32767, 32768, 53000, 88480],
            )
            gc2.put_road(hi)
            gc2.flush_leaf()
            gc2.close()
            gc3 = GeometryResultCache(root, epoch=ep1)
            gc3.open_leaf("Norway_Hedmark")
            hit_hi = gc3.get_road(b"\x11" * 16)
            self.assertEqual(
                hit_hi.samples_dm, [-5000, 0, 32767, 32768, 53000, 88480]
            )
            gc3.close()

    def test_miss_on_epoch_change(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "gcache"
            pts = [{"lat": 1.0, "lon": 2.0, "alt_m": 0.0}]
            h = road_geometry_hash(pts)
            gc = GeometryResultCache(root, epoch=policy_epoch(zoom=12))
            gc.open_leaf("X")
            gc.put_road(RoadCacheEntry(geom_hash=h, n_points=1, samples_dm=[0]))
            gc.flush_leaf()
            gc.close()
            gc2 = GeometryResultCache(root, epoch=policy_epoch(zoom=15))
            gc2.open_leaf("X")
            self.assertIsNone(gc2.get_road(h))
            gc2.close()


class TestPolicyV2(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(classify_mapterhorn_leaf("Norway_Hedmark"), "high_res")
        self.assertEqual(classify_mapterhorn_leaf("Russia_Moscow"), "copernicus_only")
        self.assertEqual(classify_mapterhorn_leaf("Latvia_Riga"), "high_res_20m")
        self.assertEqual(classify_mapterhorn_leaf("Australia_Sydney"), "mixed")
        self.assertEqual(classify_mapterhorn_leaf("Norway_Jan Mayen"), "copernicus_only")

    def test_v2_thresholds(self):
        # Copernicus-only below 60N → 5 m under v2
        self.assertEqual(
            threshold_m_for_leaf("Albania", policy_version=2, lat_max=42.0),
            5.0,
        )
        # Copernicus-only at/above 60N → 2 m
        self.assertEqual(
            threshold_m_for_leaf(
                "Russia_Murmansk Oblast", policy_version=2, lat_max=69.0
            ),
            2.0,
        )
        # high_res always 2 m under v2
        self.assertEqual(
            threshold_m_for_leaf("Norway_Hedmark", policy_version=2, lat_max=61.0),
            2.0,
        )
        # v1 always 2 m
        self.assertEqual(
            threshold_m_for_leaf("Albania", policy_version=1, lat_max=42.0),
            2.0,
        )

    def test_leaf_below_60n_heuristic(self):
        self.assertTrue(leaf_below_60n("Albania", lat_max=42.0))
        self.assertFalse(leaf_below_60n("Russia_Murmansk Oblast", lat_max=69.0))
        self.assertFalse(leaf_below_60n("Norway_Svalbard"))


class TestConcurrencyLeafSelf(unittest.TestCase):
    def test_leaf_self_and_elev_estimate_tighten_concurrency(self):
        peaks = {
            "dump": 0.1,
            "annotate": 8.2,
            "elevation": 2.0,
            "bake": 0.2,
            "leaf_self": 1.1,
        }
        # Corrected RAM: leaf_self + measured elev peak (no full mem-cache floor).
        # On media (~63 GiB free, 16 GiB headroom) this returns 6 when capped.
        n = stage_aware_process_concurrency(
            stage_peaks_gib=peaks,
            annotate_concurrency=2,
            headroom_gib=16.0,
            available_gib=63.0,
            tile_mem_cache=4096,
            max_concurrency=6,
        )
        self.assertEqual(n, 6)
        est = estimate_elevation_rss_gib(342691, mem_tiles=4096)
        # Cap at mem cache → ~3 GiB decode + base
        self.assertLess(est, 4.0)
        self.assertGreater(est, 2.0)
        # Per-leaf unique_tiles estimate can still tighten below the max.
        n_tight = stage_aware_process_concurrency(
            stage_peaks_gib=peaks,
            annotate_concurrency=2,
            headroom_gib=16.0,
            available_gib=63.0,
            tile_mem_cache=4096,
            elev_unique_tiles=342691,
            max_concurrency=6,
        )
        self.assertGreaterEqual(n_tight, 1)
        self.assertLessEqual(n_tight, 6)


if __name__ == "__main__":
    unittest.main()
