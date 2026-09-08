"""HTTP Basic Auth GET client for NPRA DATEX snapshot endpoints (stdlib only)."""

from __future__ import annotations

import base64
import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional
from urllib.parse import quote

from .auth import AuthError, Credentials, auth_failure_message
from .config import Config

# Openers used in tests: (Request, timeout) -> http.client-like response shim.
UrlOpener = Callable[[urllib.request.Request, float], object]


@dataclass
class FetchResult:
    endpoint: str
    status: int
    bytes: int
    not_modified: bool
    cached_path: Optional[Path]
    error: Optional[str] = None


class _ResponseShim:
    """Minimal file-like response for tests / urllib compatibility."""

    def __init__(self, status: int, body: bytes, headers: Dict[str, str]):
        self.status = status
        self._body = body
        self.headers = {k.lower(): v for k, v in headers.items()}

    def read(self) -> bytes:
        return self._body

    def getheader(self, name: str, default: Optional[str] = None) -> Optional[str]:
        return self.headers.get(name.lower(), default)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def endpoint_url(base_url: str, endpoint: str) -> str:
    name = endpoint.strip().strip("/")
    return f"{base_url.rstrip('/')}/datexapi/{quote(name, safe='')}/pullsnapshotdata"


def _basic_auth_header(creds: Credentials) -> str:
    token = base64.b64encode(f"{creds.username}:{creds.password}".encode("utf-8")).decode(
        "ascii"
    )
    return f"Basic {token}"


def _state_path(cfg: Config, endpoint: str) -> Path:
    return cfg.state_dir / f"{endpoint}.json"


def _load_state(cfg: Config, endpoint: str) -> dict:
    path = _state_path(cfg, endpoint)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(cfg: Config, endpoint: str, state: dict) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    path = _state_path(cfg, endpoint)
    partial = path.with_suffix(".json.partial")
    # Never persist credentials in state.
    safe = {
        k: v
        for k, v in state.items()
        if k
        in (
            "last_modified",
            "etag",
            "last_status",
            "last_bytes",
            "last_success_unix",
            "fail_count",
            "next_attempt_unix",
        )
    }
    partial.write_text(json.dumps(safe, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(path)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".partial")
    partial.write_bytes(data)
    partial.replace(path)


def write_source_metadata(cfg: Config, endpoints_ok: list[str]) -> None:
    """NPRA attribution wrapper — does not modify DATEX XML payloads."""
    meta = {
        "schema": 1,
        "source": "Statens vegvesen (Norwegian Public Roads Administration / NPRA)",
        "license": "NLOD 2.0",
        "attribution": (
            "Road information redistributed from NPRA DATEX II without distortion. "
            "NPRA must be acknowledged as the source."
        ),
        "upstream_base_url": cfg.base_url,
        "endpoints": endpoints_ok,
        "note": (
            "XML files in this directory are unmodified upstream snapshots. "
            "Credentials are not stored here."
        ),
    }
    path = cfg.publish_dir / "source.json"
    _atomic_write(path, (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def default_opener(request: urllib.request.Request, timeout: float) -> object:
    return urllib.request.urlopen(request, timeout=timeout)


def fetch_endpoint(
    cfg: Config,
    endpoint: str,
    creds: Credentials,
    *,
    opener: Optional[UrlOpener] = None,
    now: Optional[float] = None,
    jitter: bool = True,
) -> FetchResult:
    """GET one snapshot endpoint. Never logs Authorization or XML bodies."""
    if opener is None:
        opener = default_opener
    if now is None:
        now = time.time()

    state = _load_state(cfg, endpoint)
    next_attempt = float(state.get("next_attempt_unix") or 0)
    if next_attempt > now:
        # fail_count>0 => error backoff; else per-endpoint cadence hold.
        kind = "backoff" if int(state.get("fail_count") or 0) > 0 else "interval_hold"
        return FetchResult(
            endpoint=endpoint,
            status=0,
            bytes=0,
            not_modified=False,
            cached_path=cfg.publish_dir / f"{endpoint}.xml",
            error=f"{kind} until unix={int(next_attempt)}",
        )

    interval = cfg.interval_for(endpoint)
    if jitter:
        # MET Norway-style: avoid exact clock alignment (±10% of interval, capped).
        span = min(30.0, max(0.0, interval * 0.1))
        if span > 0:
            time.sleep(random.uniform(0.0, span))

    url = endpoint_url(cfg.base_url, endpoint)
    headers = {
        "User-Agent": cfg.user_agent,
        "Accept": "application/xml, text/xml, */*",
        "Authorization": _basic_auth_header(creds),
    }
    if cfg.use_if_modified_since:
        lm = state.get("last_modified")
        if lm:
            # Header name casing: HTTP is case-insensitive; live NPRA behaviour UNVERIFIED.
            headers["If-Modified-Since"] = lm

    request = urllib.request.Request(url, headers=headers, method="GET")
    # Drop Authorization from any exception stringification path we control.
    safe_url = url  # no credentials in URL by design

    try:
        with opener(request, 60.0) as resp:
            status = int(getattr(resp, "status", 200))
            body = resp.read() if status != 304 else b""
            last_mod = None
            if hasattr(resp, "getheader"):
                last_mod = resp.getheader("Last-Modified") or resp.getheader("last-modified")
            elif hasattr(resp, "headers"):
                last_mod = resp.headers.get("last-modified") or resp.headers.get("Last-Modified")
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = b""
        last_mod = None
        try:
            if exc.fp is not None:
                body = exc.read() or b""
        except Exception:
            body = b""
        if status == 401:
            raise AuthError(auth_failure_message("401")) from None
        if status == 403:
            raise AuthError(auth_failure_message("403")) from None
        fail_count = int(state.get("fail_count") or 0) + 1
        delay = min(3600, (2 ** min(fail_count, 8)) * 5)
        state["fail_count"] = fail_count
        state["next_attempt_unix"] = now + delay
        state["last_status"] = status
        _save_state(cfg, endpoint, state)
        return FetchResult(
            endpoint=endpoint,
            status=status,
            bytes=len(body),
            not_modified=False,
            cached_path=None,
            error=f"HTTP {status} for {safe_url} (backoff {delay}s)",
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        fail_count = int(state.get("fail_count") or 0) + 1
        delay = min(3600, (2 ** min(fail_count, 8)) * 5)
        state["fail_count"] = fail_count
        state["next_attempt_unix"] = now + delay
        _save_state(cfg, endpoint, state)
        # Do not include auth material; exc may stringify URL only.
        return FetchResult(
            endpoint=endpoint,
            status=0,
            bytes=0,
            not_modified=False,
            cached_path=None,
            error=f"network error for {safe_url}: {type(exc).__name__} (backoff {delay}s)",
        )

    publish_path = cfg.publish_dir / f"{endpoint}.xml"
    cache_path = cfg.cache_dir / f"{endpoint}.xml"

    if status == 304:
        state["last_status"] = 304
        state["fail_count"] = 0
        state["next_attempt_unix"] = int(now) + interval
        state["last_success_unix"] = int(now)
        if last_mod:
            state["last_modified"] = last_mod
        _save_state(cfg, endpoint, state)
        size = publish_path.stat().st_size if publish_path.is_file() else 0
        return FetchResult(
            endpoint=endpoint,
            status=304,
            bytes=size,
            not_modified=True,
            cached_path=publish_path if publish_path.is_file() else None,
        )

    if status != 200:
        fail_count = int(state.get("fail_count") or 0) + 1
        delay = min(3600, (2 ** min(fail_count, 8)) * 5)
        state["fail_count"] = fail_count
        state["next_attempt_unix"] = now + delay
        state["last_status"] = status
        _save_state(cfg, endpoint, state)
        return FetchResult(
            endpoint=endpoint,
            status=status,
            bytes=len(body),
            not_modified=False,
            cached_path=None,
            error=f"unexpected HTTP {status} for {safe_url}",
        )

    _atomic_write(cache_path, body)
    _atomic_write(publish_path, body)
    state["last_status"] = 200
    state["last_bytes"] = len(body)
    state["fail_count"] = 0
    state["next_attempt_unix"] = int(now) + interval
    state["last_success_unix"] = int(now)
    if last_mod:
        state["last_modified"] = last_mod
    _save_state(cfg, endpoint, state)
    return FetchResult(
        endpoint=endpoint,
        status=200,
        bytes=len(body),
        not_modified=False,
        cached_path=publish_path,
    )
