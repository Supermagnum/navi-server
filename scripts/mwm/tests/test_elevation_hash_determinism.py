#!/usr/bin/env python3
"""Geometry-hash collisions must not diverge populate vs cache-hit elevations."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT))

from geometry_cache import (  # noqa: E402
    QUANTUM_DEG,
    GeometryResultCache,
    RoadCacheEntry,
    overrides_from_samples,
    policy_epoch,
    quantise_coord,
    road_geometry_hash,
    sample_latlon_for_hash,
)


class TestQuantumCellSampling(unittest.TestCase):
    def test_sample_latlon_matches_hash_quantisation(self) -> None:
        # Two points inside the same 1e-5 cell (round(x/1e-5) equal).
        a = -0.900843
        b = -0.900840
        self.assertEqual(quantise_coord(a), quantise_coord(b))
        sa = sample_latlon_for_hash(a, -78.6381)
        sb = sample_latlon_for_hash(b, -78.6381)
        self.assertEqual(sa, sb)
        self.assertEqual(sa[0], quantise_coord(a) * QUANTUM_DEG)

    def test_hash_collision_same_samples_after_quantised_sample(self) -> None:
        # Real Ecuador-style collision: same quantised cells, different floats.
        p1 = [
            {"lat": -0.900843, "lon": -78.6381, "alt_m": 2862.0},
            {"lat": -0.900843, "lon": -78.6381, "alt_m": 2862.0},
        ]
        p2 = [
            {"lat": -0.900840, "lon": -78.6381, "alt_m": 2862.0},
            {"lat": -0.900843, "lon": -78.6381, "alt_m": 2862.0},
        ]
        self.assertEqual(road_geometry_hash(p1), road_geometry_hash(p2))

        def edge_sample(lat: float, lon: float) -> float:
            return 2000.0 + lat * 1e6 + lon * 1e5

        def sample_dm(points: list[dict]) -> list[int]:
            out = []
            for p in points:
                slat, slon = sample_latlon_for_hash(float(p["lat"]), float(p["lon"]))
                out.append(int(round(edge_sample(slat, slon) * 10.0)))
            return out

        self.assertEqual(sample_dm(p1), sample_dm(p2))
        full_divergent = [
            int(round(edge_sample(float(p["lat"]), float(p["lon"])) * 10.0)) for p in p1
        ] != [
            int(round(edge_sample(float(p["lat"]), float(p["lon"])) * 10.0)) for p in p2
        ]
        self.assertTrue(full_divergent)

    def test_cache_keeps_first_samples_on_collision(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            gc = GeometryResultCache(Path(td), epoch=policy_epoch(zoom=12))
            gc.open_leaf("X")
            gh = b"\x42" * 16
            gc.put_road(
                RoadCacheEntry(geom_hash=gh, n_points=2, samples_dm=[1000, 1001])
            )
            gc.put_road(
                RoadCacheEntry(
                    geom_hash=gh,
                    n_points=2,
                    samples_dm=[2000, 2001],
                    wetland=[{"segment_index": 0, "direction": 0, "class": 1}],
                )
            )
            hit = gc.get_road(gh)
            assert hit is not None
            self.assertEqual(hit.samples_dm, [1000, 1001])
            self.assertEqual(hit.wetland[0]["class"], 1)
            gc.flush_leaf()
            gc.close()

    def test_populate_vs_cache_hit_identical_with_collision(self) -> None:
        """Simulate two FID-colliding roads: cold overrides == cache-hit overrides."""
        # Ecuador_East collision shape (fids 229811 / 229812).
        p1 = [
            {"lat": -0.900843, "lon": -78.6381, "alt_m": 2862.0},
            {"lat": -0.900843, "lon": -78.6381, "alt_m": 2862.0},
        ]
        p2 = [
            {"lat": -0.900840, "lon": -78.6381, "alt_m": 2862.0},
            {"lat": -0.900843, "lon": -78.6381, "alt_m": 2862.0},
        ]
        self.assertEqual(road_geometry_hash(p1), road_geometry_hash(p2))

        def edge_sample(lat: float, lon: float) -> float:
            return 2500.0 + abs(lat) * 1e5 + abs(lon) * 1e4

        def samples_for(points: list[dict]) -> list[int]:
            out = []
            for p in points:
                slat, slon = sample_latlon_for_hash(float(p["lat"]), float(p["lon"]))
                out.append(int(round(edge_sample(slat, slon) * 10.0)))
            return out

        with tempfile.TemporaryDirectory() as td:
            gc = GeometryResultCache(Path(td), epoch=policy_epoch(zoom=12))
            gc.open_leaf("Equator")
            s1 = samples_for(p1)
            gc.put_road(
                RoadCacheEntry(
                    geom_hash=road_geometry_hash(p1), n_points=2, samples_dm=s1
                )
            )
            s2 = samples_for(p2)
            gc.put_road(
                RoadCacheEntry(
                    geom_hash=road_geometry_hash(p2), n_points=2, samples_dm=s2
                )
            )
            self.assertEqual(s1, s2)  # quantised sampling
            cold1 = overrides_from_samples(s1, [2862.0, 2862.0], 2.0)
            cold2 = overrides_from_samples(s2, [2862.0, 2862.0], 2.0)
            gc.flush_leaf()
            gc.close()

            gc2 = GeometryResultCache(Path(td), epoch=policy_epoch(zoom=12))
            gc2.open_leaf("Equator")
            hit = gc2.get_road(road_geometry_hash(p1))
            assert hit is not None
            warm1 = overrides_from_samples(hit.samples_dm, [2862.0, 2862.0], 2.0)
            warm2 = overrides_from_samples(hit.samples_dm, [2862.0, 2862.0], 2.0)
            self.assertEqual(cold1, warm1)
            self.assertEqual(cold2, warm2)
            self.assertEqual(warm1, warm2)
            gc2.close()


class TestFeatureIdRemap(unittest.TestCase):
    def test_permuted_fids_same_geometries_same_overrides(self) -> None:
        roads_a = [
            {
                "feature_id": 10,
                "points": [
                    {"lat": 1.0, "lon": 2.0, "alt_m": 100.0},
                    {"lat": 1.00001, "lon": 2.00001, "alt_m": 101.0},
                ],
            },
            {
                "feature_id": 20,
                "points": [
                    {"lat": 3.0, "lon": 4.0, "alt_m": 200.0},
                    {"lat": 3.00001, "lon": 4.00001, "alt_m": 202.0},
                ],
            },
        ]
        roads_b = [
            {
                "feature_id": 99,
                "points": list(roads_a[1]["points"]),
            },
            {
                "feature_id": 7,
                "points": list(roads_a[0]["points"]),
            },
        ]

        def fake_dm(points: list[dict]) -> list[int]:
            return [
                int(
                    round(
                        (
                            500.0
                            + sample_latlon_for_hash(float(p["lat"]), float(p["lon"]))[0]
                            * 10
                        )
                        * 10.0
                    )
                )
                for p in points
            ]

        with tempfile.TemporaryDirectory() as td:
            gc = GeometryResultCache(Path(td), epoch=policy_epoch(zoom=12))
            gc.open_leaf("Remap")
            for r in roads_a:
                pts = r["points"]
                gc.put_road(
                    RoadCacheEntry(
                        geom_hash=road_geometry_hash(pts),
                        n_points=len(pts),
                        samples_dm=fake_dm(pts),
                    )
                )
            gc.flush_leaf()
            gc.close()

            def bake(roads: list[dict]) -> dict[int, list[dict]]:
                g = GeometryResultCache(Path(td), epoch=policy_epoch(zoom=12))
                g.open_leaf("Remap")
                out: dict[int, list[dict]] = {}
                for r in roads:
                    pts = r["points"]
                    hit = g.get_road(road_geometry_hash(pts))
                    assert hit is not None
                    comaps = [float(p["alt_m"]) for p in pts]
                    out[int(r["feature_id"])] = overrides_from_samples(
                        hit.samples_dm, comaps, 2.0
                    )
                g.close()
                return out

            cold = {
                int(r["feature_id"]): overrides_from_samples(
                    fake_dm(r["points"]),
                    [float(p["alt_m"]) for p in r["points"]],
                    2.0,
                )
                for r in roads_b
            }
            warm = bake(roads_b)
            self.assertEqual(cold, warm)

    def test_changed_geometry_is_cache_miss(self) -> None:
        pts = [{"lat": 1.0, "lon": 2.0, "alt_m": 0.0}]
        pts2 = [{"lat": 1.1, "lon": 2.1, "alt_m": 0.0}]
        with tempfile.TemporaryDirectory() as td:
            gc = GeometryResultCache(Path(td), epoch=policy_epoch(zoom=12))
            gc.open_leaf("Y")
            gc.put_road(
                RoadCacheEntry(
                    geom_hash=road_geometry_hash(pts), n_points=1, samples_dm=[0]
                )
            )
            gc.flush_leaf()
            gc.close()
            gc2 = GeometryResultCache(Path(td), epoch=policy_epoch(zoom=12))
            gc2.open_leaf("Y")
            self.assertIsNotNone(gc2.get_road(road_geometry_hash(pts)))
            self.assertIsNone(gc2.get_road(road_geometry_hash(pts2)))
            gc2.close()

    def test_split_and_merge_features_cache_identity(self) -> None:
        """Split one road into two / merge two into one: unchanged geoms hit cache."""
        long_pts = [
            {"lat": 10.0, "lon": 20.0, "alt_m": 50.0},
            {"lat": 10.00002, "lon": 20.00002, "alt_m": 51.0},
            {"lat": 10.00004, "lon": 20.00004, "alt_m": 52.0},
            {"lat": 10.00006, "lon": 20.00006, "alt_m": 53.0},
        ]
        part_a = long_pts[:2]
        part_b = long_pts[2:]
        other = [
            {"lat": -5.0, "lon": 170.0, "alt_m": 10.0},
            {"lat": -5.00001, "lon": 170.00001, "alt_m": 12.0},
        ]

        def fake_dm(points: list[dict]) -> list[int]:
            return [
                int(round((100.0 + float(p["lat"]) * 1000.0 + float(p["lon"])) * 10.0))
                for p in points
            ]

        # Cold input: one long road + one short (merge candidate later).
        cold_roads = [
            {"feature_id": 1, "points": long_pts},
            {"feature_id": 2, "points": other},
        ]
        # Warm-style remapped: long split into two FIDs; other renumbered.
        split_roads = [
            {"feature_id": 100, "points": part_a},
            {"feature_id": 101, "points": part_b},
            {"feature_id": 200, "points": other},
        ]
        # Merged view: two short geoms reconstituted as the long geom.
        merge_roads = [
            {"feature_id": 50, "points": long_pts},
            {"feature_id": 51, "points": other},
        ]

        with tempfile.TemporaryDirectory() as td:
            gc = GeometryResultCache(Path(td), epoch=policy_epoch(zoom=12))
            gc.open_leaf("SplitMerge")
            for r in cold_roads + split_roads:
                pts = r["points"]
                gc.put_road(
                    RoadCacheEntry(
                        geom_hash=road_geometry_hash(pts),
                        n_points=len(pts),
                        samples_dm=fake_dm(pts),
                    )
                )
            gc.flush_leaf()
            gc.close()

            def bake(roads: list[dict]) -> dict[int, list[dict]]:
                g = GeometryResultCache(Path(td), epoch=policy_epoch(zoom=12))
                g.open_leaf("SplitMerge")
                out: dict[int, list[dict]] = {}
                for r in roads:
                    pts = r["points"]
                    hit = g.get_road(road_geometry_hash(pts))
                    assert hit is not None, f"miss for fid={r['feature_id']}"
                    out[int(r["feature_id"])] = overrides_from_samples(
                        hit.samples_dm,
                        [float(p["alt_m"]) for p in pts],
                        2.0,
                    )
                g.close()
                return out

            def cold_bake(roads: list[dict]) -> dict[int, list[dict]]:
                return {
                    int(r["feature_id"]): overrides_from_samples(
                        fake_dm(r["points"]),
                        [float(p["alt_m"]) for p in r["points"]],
                        2.0,
                    )
                    for r in roads
                }

            self.assertEqual(cold_bake(split_roads), bake(split_roads))
            self.assertEqual(cold_bake(merge_roads), bake(merge_roads))
            # Changed geometry (middle point moved) must miss.
            changed = [
                long_pts[0],
                {"lat": 10.5, "lon": 20.5, "alt_m": 51.0},
                long_pts[2],
                long_pts[3],
            ]
            g2 = GeometryResultCache(Path(td), epoch=policy_epoch(zoom=12))
            g2.open_leaf("SplitMerge")
            self.assertIsNone(g2.get_road(road_geometry_hash(changed)))
            g2.close()


if __name__ == "__main__":
    unittest.main()
