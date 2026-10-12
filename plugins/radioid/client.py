"""HTTPS client for RadioID DMR repeater list (paginated)."""

from __future__ import annotations

import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .config import Config
from .secrets import OptionalToken, redact_secrets

LOG = logging.getLogger("navi.radioid")

UrlOpener = Callable[[urllib.request.Request, float], object]


@dataclass
class FetchOutcome:
    country: str
    status: int
    rows: List[dict] = field(default_factory=list)
    error: Optional[str] = None


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


def _parse_retry_after(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    try:
        return max(0.0, float(raw.strip()))
    except ValueError:
        return None


def fetch_country_pages(
    cfg: Config,
    country: str,
    token: OptionalToken,
    *,
    opener: Optional[UrlOpener] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    rng: Optional[random.Random] = None,
) -> FetchOutcome:
    """GET all pages for country=… with per_page≤200 and throttling."""
    sleep_fn = sleeper or time.sleep
    rnd = rng or random.Random()
    open_fn = opener or (
        lambda req, timeout: urllib.request.urlopen(req, timeout=timeout)  # noqa: S310
    )
    rows: List[dict] = []
    page = 1
    pages: Optional[int] = None

    while True:
        q = urllib.parse.urlencode(
            {"country": country, "page": str(page), "per_page": str(cfg.per_page)}
        )
        url = f"{cfg.api_base.rstrip('/')}/dmr/repeater/?{q}"
        headers = {
            "User-Agent": cfg.user_agent,
            "Accept": "application/json",
        }
        if token.token:
            headers["X-API-Token"] = token.token

        last_error: Optional[str] = None
        last_status = 0
        payload: Optional[dict] = None
        attempts = cfg.max_retries + 1
        for attempt in range(attempts):
            req = urllib.request.Request(url, method="GET", headers=headers)
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
                        last_error = "rate_limited"
                        wait = ra if ra is not None else (2**attempt) + rnd.uniform(0, 1)
                        LOG.warning(
                            "country=%s page=%s status=429 attempt=%s backoff=%.2fs",
                            country,
                            page,
                            attempt + 1,
                            wait,
                        )
                        if attempt + 1 >= attempts:
                            break
                        sleep_fn(wait)
                        continue
                    if status >= 400:
                        last_status = status
                        last_error = f"http_{status}"
                        if status in (401, 403):
                            break
                        wait = (2**attempt) + rnd.uniform(0, 1)
                        if attempt + 1 >= attempts:
                            break
                        sleep_fn(wait)
                        continue
                    data = json.loads(body.decode("utf-8"))
                    if not isinstance(data, dict):
                        last_error = "bad_json"
                        last_status = status
                        break
                    payload = data
                    last_status = status
                    last_error = None
                    break
            except urllib.error.HTTPError as exc:
                last_status = int(exc.code)
                if last_status == 429:
                    ra = _parse_retry_after(exc.headers.get("Retry-After"))
                    last_error = "rate_limited"
                    wait = ra if ra is not None else (2**attempt) + rnd.uniform(0, 1)
                    if attempt + 1 >= attempts:
                        break
                    sleep_fn(wait)
                    continue
                last_error = redact_secrets(f"http_{last_status}", token.token)
                if last_status in (401, 403):
                    break
                wait = (2**attempt) + rnd.uniform(0, 1)
                if attempt + 1 >= attempts:
                    break
                sleep_fn(wait)
            except urllib.error.URLError as exc:
                last_status = 0
                last_error = redact_secrets(f"url_error:{exc.reason!s}", token.token)
                wait = (2**attempt) + rnd.uniform(0, 1)
                if attempt + 1 >= attempts:
                    break
                sleep_fn(wait)
            except Exception as exc:  # noqa: BLE001
                last_status = 0
                last_error = redact_secrets(f"error:{type(exc).__name__}", token.token)
                break

        if last_error or payload is None:
            return FetchOutcome(
                country=country, status=last_status, rows=[], error=last_error or "fetch_failed"
            )

        batch = payload.get("results") or []
        if not isinstance(batch, list):
            return FetchOutcome(
                country=country, status=last_status, rows=[], error="bad_results"
            )
        rows.extend([r for r in batch if isinstance(r, dict)])
        pages = int(payload.get("pages") or page)
        LOG.info(
            "country=%s page=%s/%s got=%s total_so_far=%s",
            country,
            page,
            pages,
            len(batch),
            len(rows),
        )
        if page >= pages:
            break
        page += 1
        if cfg.page_delay_secs > 0:
            sleep_fn(cfg.page_delay_secs)

    return FetchOutcome(country=country, status=200, rows=rows)
