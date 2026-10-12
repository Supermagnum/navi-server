"""HTTPS client for RepeaterBook exportROW.php (stdlib urllib only)."""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .config import Config
from .secrets import AppToken, redact_secrets

LOG = logging.getLogger("navi.repeaterbook")

UrlOpener = Callable[[urllib.request.Request, float], object]


@dataclass
class FetchOutcome:
    country: str
    status: int
    rows: List[dict]
    error: Optional[str] = None
    retry_after: Optional[float] = None


class _ResponseShim:
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


def export_row_url(base_url: str, country: str) -> str:
    q = urllib.parse.urlencode({"country": country})
    return f"{base_url.rstrip('/')}/exportROW.php?{q}"


def _parse_retry_after(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def parse_export_payload(payload: Any) -> List[dict]:
    """Accept ``{results:[…]}``, ``{count,results}``, or a bare list."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ("results", "result", "data", "repeaters"):
            val = payload.get(key)
            if isinstance(val, list):
                return [r for r in val if isinstance(r, dict)]
    raise ValueError("export JSON has no results list")


def _sleep(seconds: float, *, sleeper: Callable[[float], None]) -> None:
    if seconds > 0:
        sleeper(seconds)


def fetch_country(
    cfg: Config,
    country: str,
    token: AppToken,
    *,
    opener: Optional[UrlOpener] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    rng: Optional[random.Random] = None,
) -> FetchOutcome:
    """GET exportROW for one country with exponential backoff + jitter.

    On rate-limit / hard failure after retries, returns error and empty rows
    (caller must not publish).
    """
    sleep_fn = sleeper or time.sleep
    rnd = rng or random.Random()
    url = export_row_url(cfg.base_url, country)
    open_fn = opener or (
        lambda req, timeout: urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
    )

    last_error: Optional[str] = None
    last_status = 0
    last_retry_after: Optional[float] = None

    attempts = cfg.max_retries + 1
    for attempt in range(attempts):
        req = urllib.request.Request(
            url,
            method="GET",
            headers={
                "X-RB-App-Token": token.token,
                "User-Agent": cfg.user_agent,
                "Accept": "application/json",
            },
        )
        try:
            with open_fn(req, cfg.request_timeout_secs) as resp:
                status = int(getattr(resp, "status", 200))
                body = resp.read()
                if status == 429:
                    ra = _parse_retry_after(
                        resp.getheader("Retry-After")
                        if hasattr(resp, "getheader")
                        else None
                    )
                    last_status = 429
                    last_retry_after = ra
                    last_error = "rate_limited"
                    wait = ra if ra is not None else (2**attempt) + rnd.uniform(0, 1)
                    LOG.warning(
                        "country=%s status=429 attempt=%s/%s backoff=%.2fs",
                        country,
                        attempt + 1,
                        attempts,
                        wait,
                    )
                    if attempt + 1 >= attempts:
                        break
                    _sleep(wait, sleeper=sleep_fn)
                    continue
                if status >= 400:
                    last_status = status
                    last_error = f"http_{status}"
                    # Auth / UA errors: do not retry.
                    if status in (401, 403):
                        break
                    wait = (2**attempt) + rnd.uniform(0, 1)
                    if attempt + 1 >= attempts:
                        break
                    _sleep(wait, sleeper=sleep_fn)
                    continue
                try:
                    payload = json.loads(body.decode("utf-8"))
                    rows = parse_export_payload(payload)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                    last_status = status
                    last_error = f"bad_json:{type(exc).__name__}"
                    break
                return FetchOutcome(country=country, status=status, rows=rows)
        except urllib.error.HTTPError as exc:
            last_status = int(exc.code)
            ra = None
            try:
                ra = _parse_retry_after(exc.headers.get("Retry-After"))
            except Exception:  # noqa: BLE001
                ra = None
            last_retry_after = ra
            msg = redact_secrets(str(exc), token.token)
            if last_status == 429:
                last_error = "rate_limited"
                wait = ra if ra is not None else (2**attempt) + rnd.uniform(0, 1)
                LOG.warning(
                    "country=%s status=429 attempt=%s/%s backoff=%.2fs detail=%s",
                    country,
                    attempt + 1,
                    attempts,
                    wait,
                    msg,
                )
                if attempt + 1 >= attempts:
                    break
                _sleep(wait, sleeper=sleep_fn)
                continue
            last_error = f"http_{last_status}"
            if last_status in (401, 403):
                LOG.error("country=%s auth/ua failure status=%s", country, last_status)
                break
            wait = (2**attempt) + rnd.uniform(0, 1)
            if attempt + 1 >= attempts:
                break
            _sleep(wait, sleeper=sleep_fn)
        except urllib.error.URLError as exc:
            last_status = 0
            last_error = redact_secrets(f"url_error:{exc.reason!s}", token.token)
            wait = (2**attempt) + rnd.uniform(0, 1)
            LOG.warning(
                "country=%s network error attempt=%s/%s: %s",
                country,
                attempt + 1,
                attempts,
                last_error,
            )
            if attempt + 1 >= attempts:
                break
            _sleep(wait, sleeper=sleep_fn)
        except Exception as exc:  # noqa: BLE001
            last_status = 0
            last_error = redact_secrets(f"error:{type(exc).__name__}", token.token)
            break

    return FetchOutcome(
        country=country,
        status=last_status,
        rows=[],
        error=last_error or "fetch_failed",
        retry_after=last_retry_after,
    )
