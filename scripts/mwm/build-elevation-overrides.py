#!/usr/bin/env python3
"""Sample Mapterhorn directly at dumped MWM road points; emit elevation JSONL.

Input: dump_mwm_roads JSONL with feature_id + points[{lat,lon,alt_m}].
Output: elevation override JSONL for navi-mwm-sidecar bake
  {"feature_id":N,"point_count":N,"points":[{"point_index":i,"altitude_dm":d},...]}

Only points whose |Mapterhorn - CoMaps| exceeds --threshold-m (default 2.0) are kept.
Tiles cached under --cache-dir with a soft size limit.
"""
from __future__ import annotations

import argparse
import contextlib
import email.utils
import fcntl
import http.client
import io
import json
import math
import os
import random
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR / "lib"))

DEFAULT_UA = (
    "navi-server-mwm/1.0 (+https://github.com/Supermagnum/navi-server; "
    "contact=mwm-pipeline)"
)
TILE_TMPL = "https://tiles.mapterhorn.com/{z}/{x}/{y}.webp"
DEFAULT_TILE_MEM_CACHE = 4096
DEFAULT_FETCH_CONCURRENCY = 16
DEFAULT_FETCH_RETRIES = 5
DEFAULT_FETCH_TIMEOUT_S = 15.0
DEFAULT_RETRY_BASE_S = 0.5
DEFAULT_EVICT_LOW_WATERMARK_RATIO = 0.95
CACHE_BYTES_FILE = ".cache_bytes"
CACHE_LOCK_FILE = ".cache.lock"

try:  # optional runtime dependencies
    import requests as _requests  # type: ignore
    from requests.adapters import HTTPAdapter as _HTTPAdapter  # type: ignore
except ImportError:  # pragma: no cover - optional
    _requests = None
    _HTTPAdapter = None

try:  # optional runtime dependency
    import urllib3 as _urllib3  # type: ignore
except ImportError:  # pragma: no cover - optional
    _urllib3 = None


def latlon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    lat_rad = math.radians(lat)
    n = 2.0**zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def terrarium_elev(r: int, g: int, b: int) -> float:
    return (r * 256.0 + g + b / 256.0) - 32768.0


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        with tmp.open("wb") as f:
            f.write(data)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"))


@contextlib.contextmanager
def _cache_lock(cache_dir: Path):
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir / CACHE_LOCK_FILE
    with lock_path.open("a+") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def scan_cache_webp(cache_dir: Path) -> tuple[int, list[tuple[float, int, Path]]]:
    """Single fast recursive scandir walk of .webp cache files (skips dotfiles)."""
    total = 0
    entries: list[tuple[float, int, Path]] = []
    if not cache_dir.is_dir():
        return 0, entries
    stack = [str(cache_dir)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    name = entry.name
                    if name.startswith("."):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif name.endswith(".webp") and entry.is_file(follow_symlinks=False):
                            st = entry.stat(follow_symlinks=False)
                            sz = int(st.st_size)
                            total += sz
                            entries.append((float(st.st_mtime), sz, Path(entry.path)))
                    except OSError:
                        continue
        except OSError:
            continue
    return total, entries


def _read_cache_size_unlocked(cache_dir: Path) -> int | None:
    p = cache_dir / CACHE_BYTES_FILE
    if not p.is_file():
        return None
    try:
        val = int(p.read_text().strip())
        return val if val >= 0 else None
    except (OSError, ValueError):
        return None


def _write_cache_size_unlocked(cache_dir: Path, total: int) -> None:
    _atomic_write_text(cache_dir / CACHE_BYTES_FILE, f"{max(0, int(total))}\n")


def init_cache_size(cache_dir: Path, *, force_scan: bool = False) -> int:
    """Initialize tracked cache size once (from size file or single scandir walk)."""
    with _cache_lock(cache_dir):
        if not force_scan:
            existing = _read_cache_size_unlocked(cache_dir)
            if existing is not None:
                return existing
        total, _ = scan_cache_webp(cache_dir)
        _write_cache_size_unlocked(cache_dir, total)
        return total


def read_cache_size(cache_dir: Path) -> int | None:
    with _cache_lock(cache_dir):
        return _read_cache_size_unlocked(cache_dir)


def adjust_cache_size(cache_dir: Path, delta_bytes: int) -> int:
    """Atomically apply byte delta to tracked cache size under cache lock."""
    with _cache_lock(cache_dir):
        cur = _read_cache_size_unlocked(cache_dir)
        if cur is None:
            cur, _ = scan_cache_webp(cache_dir)
        updated = max(0, cur + int(delta_bytes))
        _write_cache_size_unlocked(cache_dir, updated)
        return updated


def evict_cache_lru(
    cache_dir: Path,
    max_bytes: int,
    *,
    force_scan: bool = False,
    low_watermark_ratio: float | None = None,
    reserve_bytes: int = 0,
) -> tuple[int, int, int]:
    """Single-owner (flock-serialized) LRU eviction over .webp files.

    Returns (final_bytes, evicted_count, evicted_bytes).

    Evicts until ``total + reserve_bytes <= max_bytes`` (then down to the low
    watermark when already over the cap). Callers that are about to write must
    pass ``reserve_bytes`` so the cache never exceeds the cap after the write.
    """
    if low_watermark_ratio is None:
        raw_ratio = os.environ.get(
            "NAVI_MWM_CACHE_EVICT_RATIO", str(DEFAULT_EVICT_LOW_WATERMARK_RATIO)
        )
        low_watermark_ratio = float(raw_ratio or DEFAULT_EVICT_LOW_WATERMARK_RATIO)
    ratio = min(max(float(low_watermark_ratio), 0.1), 1.0)
    reserve = max(0, int(reserve_bytes))

    with _cache_lock(cache_dir):
        entries: list[tuple[float, int, Path]] | None = None
        if force_scan:
            total, entries = scan_cache_webp(cache_dir)
            _write_cache_size_unlocked(cache_dir, total)
        else:
            tracked = _read_cache_size_unlocked(cache_dir)
            if tracked is None:
                total, entries = scan_cache_webp(cache_dir)
                _write_cache_size_unlocked(cache_dir, total)
            else:
                total = tracked

        # Hard cap: tracked size plus the bytes about to be written.
        hard_limit = max(0, int(max_bytes) - reserve)
        if total <= hard_limit and total <= max_bytes:
            return total, 0, 0

        if entries is None:
            total, entries = scan_cache_webp(cache_dir)
            if total <= hard_limit and total <= max_bytes:
                _write_cache_size_unlocked(cache_dir, total)
                return total, 0, 0

        # When over (or about to exceed) the cap, drain to the low watermark of
        # the *effective* hard limit so subsequent in-flight writes fit.
        target = int(hard_limit * ratio) if hard_limit > 0 else 0
        entries.sort(key=lambda item: (item[0], str(item[2])))
        evicted_count = 0
        evicted_bytes = 0
        for _, sz, p in entries:
            if total <= target:
                break
            try:
                actual_sz = p.stat().st_size
                p.unlink()
                total = max(0, total - actual_sz)
                evicted_count += 1
                evicted_bytes += actual_sz
            except OSError:
                continue
        _write_cache_size_unlocked(cache_dir, total)
        return total, evicted_count, evicted_bytes


def ensure_cache_room(
    cache_dir: Path,
    max_bytes: int,
    needed_bytes: int,
) -> tuple[int, int, int]:
    """Evict before a write so ``current + needed_bytes <= max_bytes``."""
    return evict_cache_lru(
        cache_dir,
        max_bytes,
        reserve_bytes=max(0, int(needed_bytes)),
    )


def write_webp_under_cap(
    cache_dir: Path,
    path: Path,
    data: bytes,
    max_bytes: int,
) -> tuple[int, int]:
    """Evict + write + account under one cache lock so parallel writers cannot overshoot.

    Returns (delta_bytes, final_tracked_bytes).
    """
    old_sz = 0
    try:
        if path.is_file():
            old_sz = path.stat().st_size
    except OSError:
        old_sz = 0
    delta = len(data) - old_sz

    with _cache_lock(cache_dir):
        tracked = _read_cache_size_unlocked(cache_dir)
        if tracked is None:
            tracked, entries = scan_cache_webp(cache_dir)
        else:
            entries = None
        hard_limit = max(0, int(max_bytes) - max(0, delta))
        if tracked > hard_limit:
            if entries is None:
                tracked, entries = scan_cache_webp(cache_dir)
            raw_ratio = os.environ.get(
                "NAVI_MWM_CACHE_EVICT_RATIO", str(DEFAULT_EVICT_LOW_WATERMARK_RATIO)
            )
            ratio = min(max(float(raw_ratio or DEFAULT_EVICT_LOW_WATERMARK_RATIO), 0.1), 1.0)
            target = int(hard_limit * ratio) if hard_limit > 0 else 0
            entries.sort(key=lambda item: (item[0], str(item[2])))
            for _, _sz, p in entries:
                if tracked <= target:
                    break
                # Never evict the file we are about to overwrite.
                if p.resolve() == path.resolve():
                    continue
                try:
                    actual_sz = p.stat().st_size
                    p.unlink()
                    tracked = max(0, tracked - actual_sz)
                except OSError:
                    continue
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(path, data)
        tracked = max(0, tracked + delta)
        _write_cache_size_unlocked(cache_dir, tracked)
        return delta, tracked


def parse_retry_after(value: str | None, *, max_wait_s: float = 120.0) -> float | None:
    if not value:
        return None
    s = value.strip()
    if not s:
        return None
    try:
        secs = float(s)
        return min(max(0.0, secs), max_wait_s)
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return min(max(0.0, delta), max_wait_s)
    except (TypeError, ValueError):
        return None


class TileHttpSession:
    """Keep-alive HTTP(S) client with retries, backoff, and Retry-After handling.

    Uses requests.Session or urllib3.PoolManager when available, and falls back
    to a thread-local http.client persistent connection pool (stdlib).
    """

    def __init__(
        self,
        *,
        concurrency: int = DEFAULT_FETCH_CONCURRENCY,
        timeout: float = DEFAULT_FETCH_TIMEOUT_S,
        retries: int = DEFAULT_FETCH_RETRIES,
        retry_base_s: float = DEFAULT_RETRY_BASE_S,
        user_agent: str | None = None,
        backend: str = "auto",
    ) -> None:
        self.concurrency = max(1, int(concurrency))
        self.timeout = float(timeout)
        self.retries = max(0, int(retries))
        self.retry_base_s = float(retry_base_s)
        self.user_agent = user_agent or os.environ.get("NAVI_MWM_HTTP_USER_AGENT", DEFAULT_UA)
        if backend == "auto":
            if _requests is not None and _HTTPAdapter is not None:
                self.backend = "requests"
            elif _urllib3 is not None:
                self.backend = "urllib3"
            else:
                self.backend = "http.client"
        else:
            self.backend = backend

        self._local = threading.local()
        self._conns_lock = threading.Lock()
        self._all_conns: list[object] = []
        self._req_session = None
        self._u3_pool = None
        if self.backend == "requests":
            if _requests is None or _HTTPAdapter is None:
                raise RuntimeError("requests backend requested but requests is not installed")
            sess = _requests.Session()
            adapter = _HTTPAdapter(
                pool_connections=self.concurrency,
                pool_maxsize=self.concurrency,
            )
            sess.mount("https://", adapter)
            sess.mount("http://", adapter)
            sess.headers.update({"User-Agent": self.user_agent})
            self._req_session = sess
        elif self.backend == "urllib3":
            if _urllib3 is None:
                raise RuntimeError("urllib3 backend requested but urllib3 is not installed")
            self._u3_pool = _urllib3.PoolManager(
                num_pools=4,
                maxsize=self.concurrency,
                headers={"User-Agent": self.user_agent},
            )

    def _get_hc_conn(self, scheme: str, host: str, port: int) -> http.client.HTTPConnection:
        conns = getattr(self._local, "conns", None)
        if conns is None:
            conns = {}
            self._local.conns = conns
        key = (scheme, host, port)
        conn = conns.get(key)
        if conn is None:
            if scheme == "https":
                conn = http.client.HTTPSConnection(host, port, timeout=self.timeout)
            else:
                conn = http.client.HTTPConnection(host, port, timeout=self.timeout)
            conns[key] = conn
            with self._conns_lock:
                self._all_conns.append(conn)
        return conn

    def _drop_hc_conn(self, scheme: str, host: str, port: int) -> None:
        conns = getattr(self._local, "conns", None)
        if not conns:
            return
        conn = conns.pop((scheme, host, port), None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def _once(self, url: str) -> tuple[int, str | None, bytes]:
        if self.backend == "requests" and self._req_session is not None:
            resp = self._req_session.get(url, timeout=self.timeout)
            return int(resp.status_code), resp.headers.get("Retry-After"), resp.content

        if self.backend == "urllib3" and self._u3_pool is not None:
            resp = self._u3_pool.request("GET", url, timeout=self.timeout, retries=False)
            return int(resp.status), resp.headers.get("Retry-After"), resp.data

        parsed = urllib.parse.urlsplit(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        port = parsed.port or (443 if scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        for sock_try in range(2):
            conn = self._get_hc_conn(scheme, host, port)
            try:
                conn.request(
                    "GET",
                    path,
                    headers={"User-Agent": self.user_agent, "Connection": "keep-alive"},
                )
                resp = conn.getresponse()
                body = resp.read()
                retry_after = resp.getheader("Retry-After")
                if resp.getheader("Connection", "").lower() == "close":
                    self._drop_hc_conn(scheme, host, port)
                return int(resp.status), retry_after, body
            except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError):
                self._drop_hc_conn(scheme, host, port)
                if sock_try == 1:
                    raise
            except Exception:
                self._drop_hc_conn(scheme, host, port)
                raise
        raise RuntimeError("unreachable")

    def get_tile(self, url: str) -> tuple[int, bytes]:
        """GET url with retries on 429, 5xx, timeouts, and transient socket errors."""
        last_err: Exception | None = None
        last_status = 0
        for attempt in range(self.retries + 1):
            retry_after_hdr: str | None = None
            try:
                status, retry_after_hdr, body = self._once(url)
                if status == 200:
                    return 200, body
                if status == 404 or (400 <= status < 500 and status != 429):
                    return status, body
                last_status = status
                last_err = RuntimeError(f"HTTP {status} for {url}")
            except (TimeoutError, socket.timeout, OSError, http.client.HTTPException) as e:
                last_err = e
            except Exception as e:
                if _requests is not None and isinstance(e, _requests.RequestException):
                    last_err = e
                elif _urllib3 is not None and isinstance(e, _urllib3.exceptions.HTTPError):
                    last_err = e
                else:
                    raise

            if attempt >= self.retries:
                break
            backoff = self.retry_base_s * (2**attempt) + random.uniform(0.0, 0.2 * self.retry_base_s)
            ra = parse_retry_after(retry_after_hdr)
            delay = max(backoff, ra) if ra is not None else backoff
            time.sleep(delay)

        if last_status:
            return last_status, b""
        if last_err is not None:
            raise last_err
        raise RuntimeError(f"tile fetch failed: {url}")

    def close(self) -> None:
        if self._req_session is not None:
            try:
                self._req_session.close()
            except Exception:
                pass
        if self._u3_pool is not None:
            try:
                self._u3_pool.clear()
            except Exception:
                pass
        with self._conns_lock:
            for conn in self._all_conns:
                try:
                    conn.close()
                except Exception:
                    pass
            self._all_conns.clear()


def _is_urlopen_mocked() -> bool:
    return getattr(urllib.request.urlopen, "__module__", "").startswith("unittest.mock")


class _RawRgbTile:
    """Compact RGB tile buffer (~786 KiB for 512x512 vs ~1 MiB PIL Image)."""

    __slots__ = ("size", "data")

    def __init__(self, size: tuple[int, int], data: bytes) -> None:
        self.size = size
        self.data = data

    def getpixel(self, xy: tuple[int, int]) -> tuple[int, int, int]:
        px, py = xy
        off = (py * self.size[0] + px) * 3
        d = self.data
        return d[off], d[off + 1], d[off + 2]


class TileCache:
    """Mapterhorn Terrarium tile cache. HTTP 404 is a soft miss (try lower zoom)."""

    _MISSING = object()  # sentinel stored in _mem for known-missing tiles

    def __init__(
        self,
        cache_dir: Path,
        max_bytes: int,
        zoom: int,
        min_zoom: int = 10,
        *,
        mem_tiles: int | None = None,
        fetch_concurrency: int | None = None,
        fetch_retries: int | None = None,
        fetch_timeout: float | None = None,
        retry_base_s: float = DEFAULT_RETRY_BASE_S,
        parent_evict: bool | None = None,
        session: TileHttpSession | None = None,
    ):
        self.cache_dir = cache_dir
        self.max_bytes = max_bytes
        self.zoom = zoom
        self.min_zoom = min_zoom
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.mem_tiles = (
            int(os.environ.get("NAVI_MWM_TILE_MEM_CACHE", str(DEFAULT_TILE_MEM_CACHE)) or DEFAULT_TILE_MEM_CACHE)
            if mem_tiles is None
            else int(mem_tiles)
        )
        self.fetch_concurrency = max(
            1,
            int(
                os.environ.get("NAVI_MWM_TILE_FETCH_CONCURRENCY", str(DEFAULT_FETCH_CONCURRENCY))
                or DEFAULT_FETCH_CONCURRENCY
            )
            if fetch_concurrency is None
            else int(fetch_concurrency),
        )
        self.fetch_retries = (
            int(os.environ.get("NAVI_MWM_TILE_FETCH_RETRIES", str(DEFAULT_FETCH_RETRIES)) or DEFAULT_FETCH_RETRIES)
            if fetch_retries is None
            else int(fetch_retries)
        )
        self.fetch_timeout = (
            float(os.environ.get("NAVI_MWM_TILE_FETCH_TIMEOUT_S", str(DEFAULT_FETCH_TIMEOUT_S)) or DEFAULT_FETCH_TIMEOUT_S)
            if fetch_timeout is None
            else float(fetch_timeout)
        )
        self.parent_evict = (
            os.environ.get("NAVI_MWM_CACHE_PARENT_EVICT", "0").strip() == "1"
            if parent_evict is None
            else bool(parent_evict)
        )
        self._mem: OrderedDict[tuple[int, int, int], object] = OrderedDict()
        tracked = None
        if (cache_dir / CACHE_BYTES_FILE).is_file():
            tracked = _read_cache_size_unlocked(cache_dir)
        self._approx_bytes = tracked if tracked is not None else 0
        self._session = session or TileHttpSession(
            concurrency=self.fetch_concurrency,
            timeout=self.fetch_timeout,
            retries=self.fetch_retries,
            retry_base_s=retry_base_s,
        )
        self._owns_session = session is None
        self.hits = 0
        self.misses = 0
        self.fetches = 0
        self.not_found = 0
        self.fallback_ok = 0

    def close(self) -> None:
        if self._owns_session and self._session is not None:
            self._session.close()
        for val in self._mem.values():
            if val is not self._MISSING and hasattr(val, "close"):
                try:
                    val.close()
                except Exception:
                    pass
        self._mem.clear()

    def _path(self, z: int, x: int, y: int) -> Path:
        return self.cache_dir / str(z) / str(x) / f"{y}.webp"

    def _miss_marker(self, z: int, x: int, y: int) -> Path:
        return self.cache_dir / str(z) / str(x) / f"{y}.404"

    def _pop_oldest_mem(self) -> None:
        while len(self._mem) > max(0, self.mem_tiles):
            _, old = self._mem.popitem(last=False)
            if old is not self._MISSING and hasattr(old, "close"):
                try:
                    old.close()
                except Exception:
                    pass

    def _remember_missing(self, key: tuple[int, int, int]) -> None:
        if self.mem_tiles <= 0:
            return
        self._mem[key] = self._MISSING
        self._pop_oldest_mem()

    def _remember_image(self, key: tuple[int, int, int], img) -> None:
        if self.mem_tiles <= 0:
            return
        self._mem[key] = img
        self._pop_oldest_mem()

    def _enforce_limit(self, force: bool = False) -> None:
        # parent_evict skips *end-of-leaf* full scans; room-before-write still runs.
        if self.parent_evict and not force:
            return
        final_bytes, _, _ = evict_cache_lru(
            self.cache_dir,
            self.max_bytes,
            force_scan=force,
        )
        self._approx_bytes = final_bytes

    def _ensure_room_for(self, needed_bytes: int) -> None:
        """Evict before writing so the cache stays at or under max_bytes."""
        if needed_bytes <= 0 or self.max_bytes <= 0:
            return
        final_bytes, _, _ = ensure_cache_room(
            self.cache_dir, self.max_bytes, needed_bytes
        )
        self._approx_bytes = final_bytes

    def _download_one_to_disk(self, z: int, x: int, y: int) -> tuple[str, bytes | None, int]:
        """Download a single missing tile into disk cache atomically.

        Returns (status_tag, bytes_or_None, bytes_added_to_cache).
        status_tag is one of: 'hit_disk', 'hit_404', 'ok', '404', 'err'.
        Evicts before write so disk cache never exceeds max_bytes.
        """
        path = self._path(z, x, y)
        miss = self._miss_marker(z, x, y)
        if miss.is_file():
            return "hit_404", None, 0
        if path.is_file():
            try:
                return "hit_disk", path.read_bytes(), 0
            except OSError:
                pass

        url = TILE_TMPL.format(z=z, x=x, y=y)
        data: bytes | None = None
        if _is_urlopen_mocked():
            req = urllib.request.Request(
                url,
                headers={"User-Agent": os.environ.get("NAVI_MWM_HTTP_USER_AGENT", DEFAULT_UA)},
            )
            try:
                with urllib.request.urlopen(req, timeout=self.fetch_timeout) as resp:
                    data = resp.read()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    _atomic_write_text(miss, "404\n")
                    return "404", None, 0
                return "err", None, 0
            except Exception:
                return "err", None, 0
        else:
            try:
                status, body = self._session.get_tile(url)
            except Exception:
                return "err", None, 0
            if status == 404:
                _atomic_write_text(miss, "404\n")
                return "404", None, 0
            if status != 200:
                return "err", None, 0
            data = body

        delta, tracked = write_webp_under_cap(
            self.cache_dir, path, data, self.max_bytes
        )
        self._approx_bytes = tracked
        return "ok", data, delta

    def prefetch_tiles(self, tile_keys: Iterable[tuple[int, int, int]]) -> dict[str, int]:
        """Collect missing tiles and fetch them concurrently with keep-alive session.

        Prefetch runs in bounded chunks; each write goes through
        ``write_webp_under_cap`` so parallel workers cannot overshoot the cap.
        """
        unique = sorted(set(tile_keys))
        missing: list[tuple[int, int, int]] = []
        for z, x, y in unique:
            if self._miss_marker(z, x, y).is_file() or self._path(z, x, y).is_file():
                continue
            missing.append((z, x, y))

        if not missing:
            return {"unique_tiles": len(unique), "missing_tiles": 0, "fetched": 0, "not_found": 0}

        # Bound in-flight prefetch: ~75 KiB/tile typical terrarium webp.
        approx_tile = int(
            os.environ.get("NAVI_MWM_CACHE_APPROX_TILE_BYTES", str(75 * 1024)) or (75 * 1024)
        )
        chunk = max(
            1,
            int(
                os.environ.get(
                    "NAVI_MWM_CACHE_PREFETCH_CHUNK",
                    str(max(self.fetch_concurrency * 4, 64)),
                )
                or 64
            ),
        )
        max_chunk_bytes = max(
            1,
            min(self.max_bytes // 20 if self.max_bytes else approx_tile, 256 * 1024**2),
        )
        chunk = min(chunk, max(1, max_chunk_bytes // max(1, approx_tile)))

        fetched = 0
        not_found = 0
        for start in range(0, len(missing), chunk):
            batch = missing[start : start + chunk]
            # Proactive room for the whole batch (individual writes still lock).
            self._ensure_room_for(len(batch) * approx_tile)
            with ThreadPoolExecutor(
                max_workers=min(self.fetch_concurrency, len(batch)),
                thread_name_prefix="tile-fetch",
            ) as pool:
                futs = {
                    pool.submit(self._download_one_to_disk, z, x, y): (z, x, y)
                    for z, x, y in batch
                }
                for fut in as_completed(futs):
                    key = futs[fut]
                    tag, _, _delta = fut.result()
                    if tag == "ok":
                        self.misses += 1
                        self.fetches += 1
                        fetched += 1
                    elif tag in ("404", "err"):
                        self.misses += 1
                        self.not_found += 1
                        not_found += 1
                        if tag == "err":
                            self._remember_missing(key)

        tracked = read_cache_size(self.cache_dir)
        if tracked is not None:
            self._approx_bytes = tracked
        return {
            "unique_tiles": len(unique),
            "missing_tiles": len(missing),
            "fetched": fetched,
            "not_found": not_found,
        }

    def _fetch_one(self, z: int, x: int, y: int):
        """Return PIL Image, or None if tile is missing (404) / unreadable."""
        from PIL import Image

        key = (z, x, y)
        if key in self._mem:
            self._mem.move_to_end(key)
            val = self._mem[key]
            if val is self._MISSING:
                self.hits += 1
                return None
            self.hits += 1
            return val

        path = self._path(z, x, y)
        miss = self._miss_marker(z, x, y)
        if miss.is_file():
            self.hits += 1
            self._remember_missing(key)
            return None

        data: bytes | None = None
        if path.is_file():
            data = path.read_bytes()
            self.hits += 1
        else:
            self.misses += 1
            tag, body, delta = self._download_one_to_disk(z, x, y)
            if tag == "ok" and body is not None:
                data = body
                self.fetches += 1
                # Size already accounted inside write_webp_under_cap.
                if delta != 0:
                    tracked = read_cache_size(self.cache_dir)
                    if tracked is not None:
                        self._approx_bytes = tracked
            elif tag == "hit_disk" and body is not None:
                data = body
                self.hits += 1
                self.misses -= 1
            else:
                self.not_found += 1
                self._remember_missing(key)
                return None

        try:
            with Image.open(io.BytesIO(data)) as opened:
                rgb = opened.convert("RGB")
                try:
                    tile = _RawRgbTile(rgb.size, rgb.tobytes())
                finally:
                    rgb.close()
        except Exception:
            self.not_found += 1
            self._remember_missing(key)
            return None
        self._remember_image(key, tile)
        return tile

    def get_image(self, lat: float, lon: float):
        """Fetch tile covering lat/lon at preferred zoom, falling back on 404."""
        for z in range(self.zoom, self.min_zoom - 1, -1):
            x, y = latlon_to_tile(lat, lon, z)
            img = self._fetch_one(z, x, y)
            if img is not None:
                if z != self.zoom:
                    self.fallback_ok += 1
                return img, z, x, y
        return None, self.zoom, 0, 0

    def sample(self, lat: float, lon: float) -> float | None:
        img, z, x, y = self.get_image(lat, lon)
        if img is None:
            return None
        n = 2.0**z
        w, h = img.size
        xf = ((lon + 180.0) / 360.0 * n - x) * w
        lat_rad = math.radians(lat)
        yf = ((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n - y) * h
        px = min(max(int(xf), 0), w - 1)
        py = min(max(int(yf), 0), h - 1)
        if isinstance(img, _RawRgbTile):
            off = (py * w + px) * 3
            d = img.data
            return terrarium_elev(d[off], d[off + 1], d[off + 2])
        r, g, b = img.getpixel((px, py))
        return terrarium_elev(r, g, b)


def collect_unique_tiles(
    roads_jsonl: Path,
    zoom: int,
    *,
    max_features: int = 0,
) -> set[tuple[int, int, int]]:
    unique: set[tuple[int, int, int]] = set()
    with roads_jsonl.open() as inp:
        for i, line in enumerate(inp):
            if max_features and i >= max_features:
                break
            if not line.strip():
                continue
            feat = json.loads(line)
            from geometry_cache import sample_latlon_for_hash  # noqa: E402

            for p in feat["points"]:
                slat, slon = sample_latlon_for_hash(float(p["lat"]), float(p["lon"]))
                x, y = latlon_to_tile(slat, slon, zoom)
                unique.add((zoom, x, y))
    return unique


def highest_working_zoom(lat: float, lon: float, preferred: int) -> int:
    for z in range(preferred, 9, -1):
        x, y = latlon_to_tile(lat, lon, z)
        url = TILE_TMPL.format(z=z, x=x, y=y)
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": DEFAULT_UA})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if int(resp.status) == 200:
                    return z
        except Exception:
            continue
    return 12


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--roads-jsonl", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cache-dir", type=Path, required=True)
    ap.add_argument("--cache-max-bytes", type=int, default=2 * 1024**3)
    ap.add_argument("--threshold-m", type=float, default=2.0)
    ap.add_argument(
        "--zoom",
        type=int,
        default=0,
        help="Mapterhorn zoom; 0 = NAVI_MWM_MAPTERHORN_ZOOM or 12 (not max-zoom)",
    )
    ap.add_argument("--fetch-concurrency", type=int, default=0)
    ap.add_argument("--tile-mem-cache", type=int, default=0)
    ap.add_argument("--no-evict", action="store_true")
    ap.add_argument("--stats-out", type=Path, default=None)
    ap.add_argument("--max-features", type=int, default=0)
    ap.add_argument(
        "--geometry-cache",
        type=Path,
        default=None,
        help="Geometry-hash cache directory (per-leaf files). Empty/omit = disabled.",
    )
    ap.add_argument(
        "--geometry-cache-write",
        action="store_true",
        help="Populate geometry cache with absolute samples for misses/all features.",
    )
    ap.add_argument(
        "--leaf",
        default="",
        help="Leaf id for per-leaf geometry-cache file (required when cache enabled).",
    )
    args = ap.parse_args()

    # Default production zoom is 12. Auto-max (highest_working_zoom→15) is a
    # mountain pilot only — corridor same-point median stays ~0.5 m at z12.
    if args.zoom <= 0:
        args.zoom = int(os.environ.get("NAVI_MWM_MAPTERHORN_ZOOM", "12") or "12")
    zoom = args.zoom if args.zoom > 0 else 12
    with args.roads_jsonl.open() as f:
        first = f.readline()
    if not first:
        args.out.write_text("")
        if args.stats_out:
            args.stats_out.write_text(
                json.dumps(
                    {
                        "zoom": zoom,
                        "threshold_m": args.threshold_m,
                        "total_points": 0,
                        "override_points": 0,
                        "override_rate": 0.0,
                        "features_with_overrides": 0,
                        "tile_cache_hits": 0,
                        "tile_cache_misses": 0,
                        "tile_cache_hit_rate": None,
                        "tile_fetches": 0,
                        "tile_not_found": 0,
                        "tile_fallback_ok": 0,
                        "elev_scan_s": 0.0,
                        "elev_fetch_s": 0.0,
                        "elev_sample_s": 0.0,
                        "wall_s": 0.0,
                    },
                    indent=2,
                )
                + "\n"
            )
        return 0

    cache = TileCache(
        args.cache_dir,
        args.cache_max_bytes,
        zoom,
        mem_tiles=args.tile_mem_cache if args.tile_mem_cache > 0 else None,
        fetch_concurrency=args.fetch_concurrency if args.fetch_concurrency > 0 else None,
        parent_evict=True if args.no_evict else None,
    )

    gcache = None
    gcache_path = args.geometry_cache
    if gcache_path is None:
        env_gc = os.environ.get("NAVI_MWM_GEOMETRY_CACHE", "").strip()
        if env_gc and os.environ.get("NAVI_MWM_GEOMETRY_CACHE_ENABLE", "1").strip() not in (
            "0",
            "false",
            "no",
            "off",
        ):
            gcache_path = Path(env_gc)
    if gcache_path is not None:
        sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
        from geometry_cache import (  # noqa: E402
            GeometryResultCache,
            RoadCacheEntry,
            overrides_from_samples,
            road_geometry_hash,
            sample_latlon_for_hash,
        )

        leaf_id = args.leaf or os.environ.get("NAVI_MWM_LEAF", "").strip()
        if not leaf_id:
            # Derive from roads filename stem when possible.
            leaf_id = args.roads_jsonl.stem.replace(".roads", "")
        gcache = GeometryResultCache(Path(gcache_path), zoom=zoom)
        gcache.open_leaf(leaf_id)
        if args.geometry_cache_write or os.environ.get(
            "NAVI_MWM_GEOMETRY_CACHE_WRITE", "1"
        ).strip() not in ("0", "false", "no", "off"):
            args.geometry_cache_write = True

    total_points = 0
    override_points = 0
    features_out = 0
    ghash_hits = 0
    ghash_misses = 0
    t0 = time.monotonic()
    prefetch_info = {"unique_tiles": 0, "missing_tiles": 0, "fetched": 0, "not_found": 0}
    try:
        t_scan0 = time.monotonic()
        # When geometry cache is warm we still collect unique tiles only for misses.
        # Cold / no-cache path prefetches everything as before.
        roads_for_prefetch = args.roads_jsonl
        miss_roads_path: Path | None = None
        if gcache is not None:
            miss_roads_path = args.out.with_suffix(".ghash-miss.roads.jsonl")
            with args.roads_jsonl.open() as inp, miss_roads_path.open("w") as miss_out:
                for i, line in enumerate(inp):
                    if args.max_features and i >= args.max_features:
                        break
                    if not line.strip():
                        continue
                    feat = json.loads(line)
                    gh = road_geometry_hash(feat["points"])
                    if gcache.get_road(gh) is not None:
                        ghash_hits += 1
                    else:
                        ghash_misses += 1
                        miss_out.write(line if line.endswith("\n") else line + "\n")
            roads_for_prefetch = miss_roads_path

        unique_tiles = collect_unique_tiles(
            roads_for_prefetch, zoom, max_features=args.max_features
        )
        elev_scan_s = time.monotonic() - t_scan0

        t_fetch0 = time.monotonic()
        prefetch_info = cache.prefetch_tiles(unique_tiles)
        elev_fetch_s = time.monotonic() - t_fetch0

        t_sample0 = time.monotonic()
        pending_cache_writes: list = []
        with args.roads_jsonl.open() as inp, args.out.open("w") as out:
            for i, line in enumerate(inp):
                if args.max_features and i >= args.max_features:
                    break
                if not line.strip():
                    continue
                feat = json.loads(line)
                fid = int(feat["feature_id"])
                points = feat["points"]
                point_count = len(points)
                comaps_alts = [float(p.get("alt_m", 0)) for p in points]
                total_points += point_count

                samples_dm: list[int | None] | None = None
                if gcache is not None:
                    gh = road_geometry_hash(points)
                    hit = gcache.get_road(gh)
                    if hit is not None and len(hit.samples_dm) == point_count:
                        samples_dm = hit.samples_dm
                    else:
                        samples_dm = []
                        for p in points:
                            # Sample at quantised cell centre so hash-equivalent
                            # geometries share one DEM pixel (tile-edge safe).
                            slat, slon = sample_latlon_for_hash(
                                float(p["lat"]), float(p["lon"])
                            )
                            mh = cache.sample(slat, slon)
                            if mh is None:
                                samples_dm.append(None)
                            else:
                                samples_dm.append(int(round(mh * 10.0)))
                        if args.geometry_cache_write:
                            # First writer for a geom_hash wins (put_road keeps it).
                            pending_cache_writes.append(
                                RoadCacheEntry(
                                    geom_hash=gh,
                                    n_points=point_count,
                                    samples_dm=samples_dm,
                                    wetland=[],
                                    boardwalk=[],
                                    wetland_touch_hashes=b"",
                                )
                            )
                    overrides = overrides_from_samples(
                        samples_dm, comaps_alts, args.threshold_m
                    )
                else:
                    overrides = []
                    for idx, p in enumerate(points):
                        slat, slon = sample_latlon_for_hash(
                            float(p["lat"]), float(p["lon"])
                        )
                        mh = cache.sample(slat, slon)
                        if mh is None:
                            continue
                        comaps = float(p.get("alt_m", 0))
                        if abs(mh - comaps) <= args.threshold_m:
                            continue
                        dm = int(round(mh * 10.0))
                        # i32 absolute dm (sidecar elevation body v2 / EL02).
                        overrides.append({"point_index": idx, "altitude_dm": dm})

                override_points += len(overrides)
                if not overrides:
                    continue
                out.write(
                    json.dumps(
                        {
                            "feature_id": fid,
                            "point_count": point_count,
                            "points": overrides,
                        },
                        separators=(",", ":"),
                    )
                    + "\n"
                )
                features_out += 1
        if gcache is not None and pending_cache_writes:
            gcache.put_roads_many(pending_cache_writes)
            gcache.flush_leaf()
        elev_sample_s = time.monotonic() - t_sample0

        if miss_roads_path is not None:
            try:
                miss_roads_path.unlink(missing_ok=True)
            except OSError:
                pass

        if not cache.parent_evict:
            cache._enforce_limit()
    finally:
        cache.close()
        if gcache is not None:
            gcache.close()

    lookups = cache.hits + cache.misses
    wall_s = time.monotonic() - t0
    ghash_lookups = ghash_hits + ghash_misses
    stats = {
        "zoom": zoom,
        "threshold_m": args.threshold_m,
        "total_points": total_points,
        "override_points": override_points,
        "override_rate": (override_points / total_points) if total_points else 0.0,
        "features_with_overrides": features_out,
        "unique_tiles": prefetch_info["unique_tiles"],
        "tile_cache_hits": cache.hits,
        "tile_cache_misses": cache.misses,
        "tile_cache_hit_rate": (cache.hits / lookups) if lookups else None,
        "tile_fetches": cache.fetches,
        "tile_not_found": cache.not_found,
        "tile_fallback_ok": cache.fallback_ok,
        "tile_mem_cache": cache.mem_tiles,
        "tile_fetch_concurrency": cache.fetch_concurrency,
        "cache_bytes_approx": cache._approx_bytes,
        "cache_dir": str(args.cache_dir),
        "cache_max_bytes": args.cache_max_bytes,
        "geometry_hash_hits": ghash_hits,
        "geometry_hash_misses": ghash_misses,
        "geometry_hash_hit_rate": (ghash_hits / ghash_lookups) if ghash_lookups else None,
        "elev_scan_s": round(elev_scan_s, 3),
        "elev_fetch_s": round(elev_fetch_s, 3),
        "elev_sample_s": round(elev_sample_s, 3),
        "wall_s": round(wall_s, 3),
    }
    if args.stats_out:
        args.stats_out.write_text(json.dumps(stats, indent=2) + "\n")
    print(json.dumps(stats), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
