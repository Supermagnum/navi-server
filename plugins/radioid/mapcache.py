"""Conditional GET + stream-parse of RadioID map.json (coordinates)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, Mapping, Optional, Set

from .config import Config
from .secrets import OptionalToken, redact_secrets

LOG = logging.getLogger("navi.radioid")

UrlOpener = Callable[[urllib.request.Request, float], object]


def _state_path(cfg: Config) -> Path:
    return cfg.work_dir / "state" / "map.json.meta"


def _cache_path(cfg: Config) -> Path:
    return cfg.work_dir / "cache" / "map.json"


def _load_meta(cfg: Config) -> dict:
    path = _state_path(cfg)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_meta(cfg: Config, meta: dict) -> None:
    path = _state_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    partial.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(path)


def fetch_map_file(
    cfg: Config,
    token: OptionalToken,
    *,
    opener: Optional[UrlOpener] = None,
) -> Path:
    """Download map.json at most once per call; reuse cache on 304."""
    cache = _cache_path(cfg)
    cache.parent.mkdir(parents=True, exist_ok=True)
    meta = _load_meta(cfg)
    headers = {
        "User-Agent": cfg.user_agent,
        "Accept": "application/json",
    }
    if token.token:
        headers["X-API-Token"] = token.token
    if meta.get("etag"):
        headers["If-None-Match"] = str(meta["etag"])
    if meta.get("last_modified"):
        headers["If-Modified-Since"] = str(meta["last_modified"])

    open_fn = opener or (
        lambda req, timeout: urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
    )
    req = urllib.request.Request(cfg.map_url, method="GET", headers=headers)
    try:
        with open_fn(req, cfg.request_timeout_secs) as resp:
            status = int(getattr(resp, "status", 200))
            if status == 304 and cache.is_file():
                LOG.info("map.json not modified (304); reusing cache path=%s", cache)
                return cache
            body = resp.read()
            etag = None
            last_mod = None
            if hasattr(resp, "getheader"):
                etag = resp.getheader("ETag")
                last_mod = resp.getheader("Last-Modified")
            partial = cache.with_suffix(".partial")
            partial.write_bytes(body)
            partial.replace(cache)
            _save_meta(
                cfg,
                {
                    "etag": etag,
                    "last_modified": last_mod,
                    "bytes": len(body),
                    "url": cfg.map_url,
                },
            )
            LOG.info("map.json downloaded bytes=%s path=%s", len(body), cache)
            return cache
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and cache.is_file():
            LOG.info("map.json not modified (HTTPError 304); reusing cache")
            return cache
        raise RuntimeError(
            redact_secrets(f"map.json fetch failed: HTTP {exc.code}", token.token)
        ) from exc
    except urllib.error.URLError as exc:
        if cache.is_file():
            LOG.warning(
                "map.json network error (%s); reusing stale cache",
                redact_secrets(str(exc.reason), token.token),
            )
            return cache
        raise RuntimeError(
            redact_secrets(f"map.json fetch failed: {exc.reason!s}", token.token)
        ) from exc


def iter_markers(path: Path) -> Iterator[dict]:
    """Stream-parse map.json markers array with bounded decode buffers."""
    decoder = json.JSONDecoder()
    with path.open("r", encoding="utf-8") as f:
        buf = ""
        # Find "markers" array start.
        while True:
            chunk = f.read(65536)
            if not chunk and not buf:
                raise ValueError("map.json: markers array not found")
            buf += chunk
            idx = buf.find('"markers"')
            if idx < 0:
                if len(buf) > 1_000_000:
                    buf = buf[-200_000:]
                continue
            rest = buf[idx:]
            bracket = rest.find("[")
            if bracket < 0:
                continue
            buf = rest[bracket + 1 :]
            break

        while True:
            buf = buf.lstrip()
            if buf.startswith("]"):
                return
            if buf.startswith(","):
                buf = buf[1:].lstrip()
                continue
            if not buf:
                chunk = f.read(65536)
                if not chunk:
                    return
                buf += chunk
                continue
            try:
                obj, end = decoder.raw_decode(buf)
            except json.JSONDecodeError:
                chunk = f.read(65536)
                if not chunk:
                    raise
                buf += chunk
                if len(buf) > 8_000_000:
                    raise ValueError("map.json: marker object too large / parse stuck")
                continue
            if isinstance(obj, dict):
                yield obj
            buf = buf[end:]


def load_coords_for_countries(
    path: Path, countries: Iterable[str]
) -> Dict[int, dict]:
    """Return locator → {lat, lng, ...} for configured countries only."""
    wanted = {c.strip().lower() for c in countries if c.strip()}
    out: Dict[int, dict] = {}
    for m in iter_markers(path):
        country = str(m.get("country") or "").strip().lower()
        if country not in wanted:
            continue
        loc = m.get("locator")
        if loc is None:
            loc = m.get("id")
        try:
            loc_i = int(loc)
        except (TypeError, ValueError):
            continue
        out[loc_i] = m
    return out
