#!/usr/bin/env python3
"""Tests for MWM elevation speedups: cache accounting, atomic writes,
concurrent keep-alive fetch with 429/5xx/timeout retries, single-owner LRU
eviction, processed.json flock race, download budget, stage-aware concurrency,
annotate semaphore, and schedule guard.
"""
from __future__ import annotations

import fcntl
import importlib.util
import io
import json
import multiprocessing
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
MWM_DIR = ROOT / "scripts" / "mwm"
sys.path.insert(0, str(MWM_DIR / "lib"))
sys.path.insert(0, str(MWM_DIR))

from leaf_scheduler import (  # noqa: E402
    ScheduleGuardConfig,
    evaluate_schedule_guard,
    resolve_process_concurrency,
    stage_aware_process_concurrency,
)


def _load_elev():
    spec = importlib.util.spec_from_file_location(
        "build_elevation_overrides", MWM_DIR / "build-elevation-overrides.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _load_process_leaf():
    spec = importlib.util.spec_from_file_location(
        "process_comaps_leaf", MWM_DIR / "process_comaps_leaf.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "run_parallel_leaves", MWM_DIR / "run_parallel_leaves.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _make_terrarium_webp(elev_m: float = 100.0) -> bytes:
    base = elev_m + 32768.0
    r = int(base // 256)
    g = int(base - r * 256)
    b = 0
    img = Image.new("RGB", (8, 8), (r, g, b))
    buf = io.BytesIO()
    img.save(buf, format="WEBP", lossless=True)
    return buf.getvalue()


def _record_worker(args):
    state_path, leaf, mwm_path, sc_path, discovery, wall_s = args
    mod = _load_process_leaf()
    mod.record_processed(
        Path(state_path),
        leaf,
        Path(mwm_path),
        Path(sc_path),
        discovery,
        wall_s=wall_s,
    )


def _annotate_slot_worker(args):
    lock_dir, max_slots, active_counter, max_observed, lock = args
    mod = _load_process_leaf()
    with mod.acquire_annotate_slot(Path(lock_dir), max_slots, poll_s=0.01):
        with lock:
            active_counter.value += 1
            if active_counter.value > max_observed.value:
                max_observed.value = active_counter.value
        time.sleep(0.08)
        with lock:
            active_counter.value -= 1


class TestCacheAccountingAndEviction(unittest.TestCase):
    def test_init_adjust_and_no_rglob_during_fetch(self):
        elev = _load_elev()
        with tempfile.TemporaryDirectory() as td:
            cdir = Path(td)
            p1 = cdir / "12" / "100" / "200.webp"
            p1.parent.mkdir(parents=True, exist_ok=True)
            p1.write_bytes(b"a" * 123)
            p2 = cdir / "12" / "100" / "201.webp"
            p2.write_bytes(b"b" * 456)

            total = elev.init_cache_size(cdir, force_scan=True)
            self.assertEqual(total, 579)
            self.assertEqual(elev.read_cache_size(cdir), 579)

            updated = elev.adjust_cache_size(cdir, 100)
            self.assertEqual(updated, 679)
            self.assertEqual(elev.read_cache_size(cdir), 679)

            cache = elev.TileCache(
                cdir,
                max_bytes=10 * 1024**2,
                zoom=12,
                min_zoom=12,
                parent_evict=True,
            )
            try:
                with mock.patch.object(
                    Path, "rglob", side_effect=AssertionError("rglob must not be called")
                ):
                    with mock.patch.object(
                        cache._session, "get_tile", return_value=(200, _make_terrarium_webp(50.0))
                    ):
                        res = cache.prefetch_tiles([(12, 100, 202), (12, 100, 203)])
                        self.assertEqual(res["fetched"], 2)
                        cache._enforce_limit()  # parent_evict=True -> no-op
            finally:
                cache.close()

            expected_new = (cdir / "12" / "100" / "202.webp").stat().st_size + (
                cdir / "12" / "100" / "203.webp"
            ).stat().st_size
            self.assertEqual(elev.read_cache_size(cdir), 679 + expected_new)

    def test_atomic_tile_writes_no_leftover_tmp(self):
        elev = _load_elev()
        webp = _make_terrarium_webp(25.0)
        with tempfile.TemporaryDirectory() as td:
            cdir = Path(td)
            cache = elev.TileCache(cdir, max_bytes=10**7, zoom=12, min_zoom=12)
            try:
                def fake_get(url):
                    if url.endswith("/999.webp"):
                        return 404, b""
                    return 200, webp

                with mock.patch.object(cache._session, "get_tile", side_effect=fake_get):
                    cache.prefetch_tiles([(12, 1, 2), (12, 1, 999)])

                self.assertEqual((cdir / "12" / "1" / "2.webp").read_bytes(), webp)
                self.assertEqual((cdir / "12" / "1" / "999.404").read_text(), "404\n")
                tmps = [p for p in cdir.rglob("*.tmp.*")]
                self.assertEqual(tmps, [])
            finally:
                cache.close()

    def test_single_owner_eviction_lru_and_lock(self):
        elev = _load_elev()
        with tempfile.TemporaryDirectory() as td:
            cdir = Path(td)
            tiles = []
            now = time.time()
            for i in range(5):
                p = cdir / "12" / "1" / f"{i}.webp"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(bytes([i]) * 100)
                os.utime(p, (now - (10 - i) * 10, now - (10 - i) * 10))
                tiles.append(p)

            elev.init_cache_size(cdir, force_scan=True)
            self.assertEqual(elev.read_cache_size(cdir), 500)

            active_evictors = 0
            max_concurrent = 0
            state_lock = threading.Lock()
            orig_scan = elev.scan_cache_webp

            def tracked_scan(path):
                nonlocal active_evictors, max_concurrent
                with state_lock:
                    active_evictors += 1
                    max_concurrent = max(max_concurrent, active_evictors)
                time.sleep(0.05)
                try:
                    return orig_scan(path)
                finally:
                    with state_lock:
                        active_evictors -= 1

            with mock.patch.object(elev, "scan_cache_webp", side_effect=tracked_scan):
                threads = [
                    threading.Thread(
                        target=elev.evict_cache_lru,
                        args=(cdir, 250),
                        kwargs={"low_watermark_ratio": 0.9},
                    )
                    for _ in range(4)
                ]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

            self.assertEqual(max_concurrent, 1)
            final_sz = elev.read_cache_size(cdir)
            self.assertLessEqual(final_sz, 250)
            # Oldest (0, 1, 2) evicted; newest (3, 4) kept
            self.assertFalse(tiles[0].exists())
            self.assertFalse(tiles[1].exists())
            self.assertFalse(tiles[2].exists())
            self.assertTrue(tiles[3].exists())
            self.assertTrue(tiles[4].exists())

    def test_mem_cache_default_4096_and_bounded(self):
        elev = _load_elev()
        with tempfile.TemporaryDirectory() as td:
            cdir = Path(td)
            default_cache = elev.TileCache(cdir, max_bytes=10**8, zoom=12, min_zoom=12)
            try:
                self.assertEqual(default_cache.mem_tiles, 4096)
            finally:
                default_cache.close()

            small_cache = elev.TileCache(cdir, max_bytes=10**8, zoom=12, min_zoom=12, mem_tiles=2)
            webp = _make_terrarium_webp(10.0)
            try:
                for y in range(4):
                    p = small_cache._path(12, 1, y)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(webp)
                    small_cache._fetch_one(12, 1, y)
                self.assertEqual(len(small_cache._mem), 2)
                self.assertEqual(list(small_cache._mem.keys()), [(12, 1, 2), (12, 1, 3)])
            finally:
                small_cache.close()


class TestConcurrentFetchMockServer(unittest.TestCase):
    def test_concurrent_fetch_retries_429_5xx_timeout_and_keepalive(self):
        elev = _load_elev()
        webp = _make_terrarium_webp(123.0)
        attempts: dict[str, int] = {}
        seen_uas: list[str] = []
        conn_ids: set[int] = set()
        lock = threading.Lock()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):  # noqa: A002
                return

            def do_GET(self):  # noqa: N802
                with lock:
                    conn_ids.add(id(self.connection))
                    seen_uas.append(self.headers.get("User-Agent", ""))
                    n = attempts.get(self.path, 0) + 1
                    attempts[self.path] = n

                if self.path == "/12/10/10.webp" and n == 1:
                    body = b"rate limited"
                    self.send_response(429)
                    self.send_header("Retry-After", "0.05")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/12/10/11.webp" and n <= 2:
                    body = b"unavailable"
                    self.send_response(503)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if self.path == "/12/10/12.webp" and n == 1:
                    time.sleep(0.35)  # exceed 0.15s timeout
                    try:
                        self.send_response(200)
                        self.send_header("Content-Length", str(len(webp)))
                        self.end_headers()
                        self.wfile.write(webp)
                    except OSError:
                        pass
                    return
                if self.path == "/12/10/99.webp":
                    body = b"not found"
                    self.send_response(404)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                self.send_response(200)
                self.send_header("Content-Type", "image/webp")
                self.send_header("Content-Length", str(len(webp)))
                self.end_headers()
                self.wfile.write(webp)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        srv_thread = threading.Thread(target=server.serve_forever, daemon=True)
        srv_thread.start()

        try:
            with tempfile.TemporaryDirectory() as td:
                cdir = Path(td)
                sess = elev.TileHttpSession(
                    concurrency=4,
                    timeout=0.15,
                    retries=4,
                    retry_base_s=0.02,
                    user_agent="navi-test-ua/1.0",
                    backend="http.client",
                )
                cache = elev.TileCache(
                    cdir,
                    max_bytes=10 * 1024**2,
                    zoom=12,
                    min_zoom=12,
                    fetch_concurrency=4,
                    session=sess,
                )
                tiles = [(12, 10, y) for y in range(10, 30)] + [(12, 10, 99)]
                tmpl = f"http://127.0.0.1:{port}/{{z}}/{{x}}/{{y}}.webp"
                try:
                    with mock.patch.object(elev, "TILE_TMPL", tmpl):
                        info = cache.prefetch_tiles(tiles)
                finally:
                    cache.close()
                    sess.close()

                self.assertEqual(info["unique_tiles"], 21)
                self.assertEqual(info["fetched"], 20)
                self.assertEqual(info["not_found"], 1)
                self.assertTrue((cdir / "12" / "10" / "99.404").is_file())
                self.assertGreaterEqual(attempts["/12/10/10.webp"], 2)
                self.assertGreaterEqual(attempts["/12/10/11.webp"], 3)
                self.assertGreaterEqual(attempts["/12/10/12.webp"], 2)
                self.assertEqual(attempts["/12/10/99.webp"], 1)
                self.assertTrue(all(ua == "navi-test-ua/1.0" for ua in seen_uas))
                # Keep-alive reused sockets across 20+ requests with 4 workers
                self.assertLess(len(conn_ids), len(seen_uas))
        finally:
            server.shutdown()
            server.server_close()


class TestProcessedRaceAndScheduler(unittest.TestCase):
    def test_record_processed_concurrent_processes_no_lost_fields(self):
        with tempfile.TemporaryDirectory() as td:
            tdir = Path(td)
            state_path = tdir / "processed.json"
            state_path.write_text('{"release_version": 261105, "leaves": {}}\n')
            mwm = tdir / "dummy.mwm"
            sc = tdir / "dummy.zst"
            mwm.write_bytes(b"mwm-payload")
            sc.write_bytes(b"sidecar-payload")
            leaves = [f"Leaf_{i}" for i in range(12)]
            discovery = {
                "version": 261105,
                "leaves": {lf: {"size": 11, "sha1_base64": "abc"} for lf in leaves},
            }

            ctx = multiprocessing.get_context("spawn")
            tasks = [
                (str(state_path), lf, str(mwm), str(sc), discovery, 10.0 + i)
                for i, lf in enumerate(leaves)
            ]
            with ctx.Pool(processes=6) as pool:
                pool.map(_record_worker, tasks)

            st = json.loads(state_path.read_text())
            self.assertEqual(set(st["leaves"].keys()), set(leaves))
            for i, lf in enumerate(leaves):
                rec = st["leaves"][lf]
                self.assertEqual(rec["wall_s"], 10.0 + i)
                self.assertEqual(rec["mwm_bytes"], 11)
                self.assertIn("mwm_sha256", rec)
                self.assertIn("sidecar_sha256", rec)

    def test_download_budget_bounds_and_releases(self):
        runner = _load_runner()
        budget = runner.DownloadBudget(budget_bytes=2000, initial_used_bytes=2500)
        acquired = []

        def waiter():
            ok = budget.acquire(500, poll_s=0.01)
            acquired.append((ok, budget.used_bytes))

        t = threading.Thread(target=waiter, daemon=True)
        t.start()
        time.sleep(0.05)
        self.assertEqual(acquired, [])  # blocked while used_bytes=2500 > 2000

        budget.release(1000)  # used_bytes -> 1500, 1500 + 500 <= 2000
        t.join(timeout=2.0)
        self.assertEqual(acquired, [(True, 2000)])

    def test_stage_aware_concurrency_and_annotate_semaphore(self):
        # 41 GiB avail, 16 headroom -> 25 usable.
        # leaf_self 1.1; elev uses measured peak (no full mem-cache floor) →
        # ann=9.3 / other=2.9 → 2*9.3 + 2*2.9 = 24.4 <= 25 → concurrency 4.
        # (Previously the mem-cache floor forced other≈4.4 and concurrency 3.)
        n = stage_aware_process_concurrency(
            stage_peaks_gib={
                "dump": 0.5,
                "annotate": 8.2,
                "elevation": 1.8,
                "bake": 0.3,
                "leaf_self": 1.1,
            },
            annotate_concurrency=2,
            headroom_gib=16.0,
            available_gib=41.0,
            tile_mem_cache=4096,
        )
        self.assertEqual(n, 4)

        with tempfile.TemporaryDirectory() as td:
            peaks_file = Path(td) / "peaks.json"
            peaks_file.write_text(
                json.dumps(
                    {
                        "stage_peaks_gib": {
                            "dump": 0.5,
                            "annotate": 8.2,
                            "elevation": 1.8,
                            "bake": 0.3,
                            "leaf_self": 1.1,
                        },
                        "annotate_concurrency": 2,
                        "tile_mem_cache": 4096,
                        "max_process_concurrency": 6,
                    }
                )
            )
            with mock.patch.dict(
                os.environ,
                {
                    "NAVI_MWM_PROCESS_CONCURRENCY": "0",
                    "NAVI_MWM_PER_LEAF_GIB": "",
                    "NAVI_MWM_RAM_HEADROOM_GIB": "16",
                    "NAVI_MWM_MEASURED_PEAKS": str(peaks_file),
                },
            ):
                self.assertEqual(resolve_process_concurrency(available_gib=41.0), 4)

            lock_dir = Path(td) / "ann_locks"
            mgr = multiprocessing.Manager()
            active = mgr.Value("i", 0)
            max_obs = mgr.Value("i", 0)
            mlock = mgr.Lock()
            ctx = multiprocessing.get_context("spawn")
            with ctx.Pool(processes=4) as pool:
                pool.map(
                    _annotate_slot_worker,
                    [(str(lock_dir), 2, active, max_obs, mlock) for _ in range(4)],
                )
            self.assertEqual(max_obs.value, 2)

    def test_schedule_guard_weekly_bake_window(self):
        with tempfile.TemporaryDirectory() as td:
            done_marker = Path(td) / "bake.done"
            lock_file = Path(td) / "weekly.lock"
            cfg = ScheduleGuardConfig(
                enabled=True,
                weekly_weekday_utc=0,  # Monday
                weekly_hour_utc=0,
                weekly_minute_utc=0,
                pause_before_hours=6.0,
                pause_after_hours=6.0,
                done_marker_path=done_marker,
                lock_path=lock_file,
            )

            # Sunday 2026-11-15 17:59 UTC -> outside window
            p, _, _ = evaluate_schedule_guard(datetime(2026, 11, 15, 17, 59, tzinfo=timezone.utc), cfg)
            self.assertFalse(p)

            # Sunday 2026-11-15 18:00 UTC (6h before Mon 00:00) -> pause
            p, reason, _ = evaluate_schedule_guard(
                datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc), cfg
            )
            self.assertTrue(p, reason)

            # Monday 2026-11-16 01:00 UTC -> pause
            p, _, _ = evaluate_schedule_guard(datetime(2026, 11, 16, 1, 0, tzinfo=timezone.utc), cfg)
            self.assertTrue(p)

            # Touch done_marker after Monday 00:00 UTC -> resumes
            done_marker.write_text("ok\n")
            bake_ts = datetime(2026, 11, 16, 0, 20, tzinfo=timezone.utc).timestamp()
            os.utime(done_marker, (bake_ts, bake_ts))
            p, reason, _ = evaluate_schedule_guard(
                datetime(2026, 11, 16, 1, 0, tzinfo=timezone.utc), cfg
            )
            self.assertFalse(p, reason)

            # Monday 2026-11-16 06:00 UTC (past pause_after) -> outside window
            done_marker.unlink()
            p, _, _ = evaluate_schedule_guard(datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc), cfg)
            self.assertFalse(p)

            # External weekly lock held -> pauses even outside time window
            with lock_file.open("w") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                p, reason, _ = evaluate_schedule_guard(
                    datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc), cfg
                )
                self.assertTrue(p, reason)
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            # Disabled -> never pauses
            off_cfg = ScheduleGuardConfig(enabled=False)
            p, _, _ = evaluate_schedule_guard(
                datetime(2026, 11, 15, 20, 0, tzinfo=timezone.utc), off_cfg
            )
            self.assertFalse(p)


if __name__ == "__main__":
    unittest.main()
