"""Generic DATEX publication fetcher (all auth types). Never logs secrets."""

from __future__ import annotations

import gzip
import json
import logging
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .auth_types import AuthMaterial, apply_auth_headers, redact_secrets
from .provider_spec import ProviderSpec, Publication
from .publish import atomic_write_bytes, provider_publish_dir, write_source_json

LOG = logging.getLogger("navi.datex.fetcher")

UrlOpener = Callable[[urllib.request.Request, float], object]
_HREF_XML_RE = re.compile(r"""href=["']([^"']+\.xml)["']""", re.IGNORECASE)


@dataclass
class PubFetchResult:
    name: str
    status: int
    bytes: int
    not_modified: bool
    error: Optional[str] = None
    path: Optional[Path] = None


def _state_path(state_dir: Path, pub_name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in pub_name)
    return state_dir / f"{safe}.json"


def _load_state(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
            "last_error",
        )
    }
    # Never persist secrets; scrub error strings
    if "last_error" in safe and isinstance(safe["last_error"], str):
        safe["last_error"] = redact_secrets(safe["last_error"])[:500]
    partial = path.with_suffix(".json.partial")
    partial.write_text(json.dumps(safe, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(path)


def detect_datex_version(xml_bytes: bytes) -> str:
    """Return coarse version label: 3.x / 2.x / unknown."""
    head = xml_bytes[:8192].decode("utf-8", errors="replace")
    if (
        "datex2.eu/schema/3/" in head
        or "messageContainer" in head
        or "EnergyInfrastructure" in head
    ):
        return "3.x"
    if "datex2.eu/schema/2/" in head or "d2LogicalModel" in head:
        return "2.x"
    return "unknown"


def validate_datex_xml(xml_bytes: bytes, expect_version: str) -> tuple[bool, str]:
    if not xml_bytes or b"<" not in xml_bytes[:500]:
        return False, "not XML"
    detected = detect_datex_version(xml_bytes)
    exp = (expect_version or "").strip()
    if not exp:
        # Empty expect: accept any DATEX-like payload (directory aggregates).
        if detected == "unknown" and not any(
            m in xml_bytes[:8192]
            for m in (
                b"SituationPublication",
                b"payloadPublication",
                b"MeasuredDataPublication",
                b"EnergyInfrastructure",
                b"naviDatexDirectoryAggregate",
            )
        ):
            return False, "not DATEX-like XML"
        return True, detected or "aggregate"
    if exp.startswith("3") and not detected.startswith("3"):
        return False, f"expected DATEX 3.x, detected {detected}"
    if exp.startswith("2") and not detected.startswith("2"):
        return False, f"expected DATEX 2.x, detected {detected}"
    return True, detected


def _maybe_gunzip(body: bytes) -> bytes:
    if len(body) >= 2 and body[0] == 0x1F and body[1] == 0x8B:
        return gzip.decompress(body)
    return body


def resolve_publication_url(
    spec: ProviderSpec,
    pub: Publication,
    *,
    now: Optional[float] = None,
    day_offset: int = 0,
) -> str:
    """Expand {YYYYMMDD} in publication URLs (Bulgaria RIA dated files)."""
    url = pub.url
    if "{YYYYMMDD}" not in url:
        return url
    tz_name = (pub.url_date_tz or spec.url_date_tz or "UTC").strip() or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"invalid url_date_tz {tz_name!r}: {exc}") from exc
    if now is None:
        now = time.time()
    day = datetime.fromtimestamp(now, tz) + timedelta(days=day_offset)
    return url.replace("{YYYYMMDD}", day.strftime("%Y%m%d"))


def _absolute_href(base_url: str, href: str) -> str:
    return urllib.parse.urljoin(base_url if base_url.endswith("/") else base_url + "/", href)


def list_xml_hrefs_from_html(html: bytes, base_url: str) -> List[str]:
    """Return absolute *.xml URLs from an Apache-style directory listing."""
    text = html.decode("utf-8", errors="replace")
    found: List[Tuple[int, str]] = []
    seen = set()
    for match in _HREF_XML_RE.finditer(text):
        href = match.group(1).strip()
        if not href or href.startswith("?") or href.startswith("../"):
            continue
        abs_url = _absolute_href(base_url, href)
        if abs_url in seen:
            continue
        seen.add(abs_url)
        base = href.rsplit("/", 1)[-1]
        num = None
        m = re.match(r"^(\d+)\.xml$", base, re.IGNORECASE)
        if m:
            num = int(m.group(1))
        found.append((num if num is not None else -1, abs_url))
    # Prefer highest numeric basename (TIPI numbered events); else stable alpha.
    found.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [u for _, u in found]


def build_directory_aggregate(parts: List[Tuple[str, bytes]], *, source_url: str) -> bytes:
    """Wrap validated child DATEX XML files in a navi aggregate root."""
    chunks = [
        b'<?xml version="1.0" encoding="UTF-8"?>\n',
        (
            b'<naviDatexDirectoryAggregate xmlns="urn:navi-server:datex-directory-aggregate:1" '
            + f'source="{source_url}" count="{len(parts)}">\n'.encode("utf-8")
        ),
    ]
    for name, body in parts:
        safe = name.replace("--", "-")
        chunks.append(f"<!-- begin {safe} -->\n".encode("utf-8"))
        # Strip XML declaration from children to keep one prolog.
        text = body.lstrip()
        if text.startswith(b"<?xml"):
            end = text.find(b"?>")
            if end != -1:
                text = text[end + 2 :].lstrip()
        chunks.append(text)
        if not text.endswith(b"\n"):
            chunks.append(b"\n")
        chunks.append(f"<!-- end {safe} -->\n".encode("utf-8"))
    chunks.append(b"</naviDatexDirectoryAggregate>\n")
    return b"".join(chunks)


def fetch_publication(
    spec: ProviderSpec,
    pub: Publication,
    auth: AuthMaterial,
    *,
    pack_root: Path,
    state_dir: Path,
    opener: Optional[UrlOpener] = None,
    now: Optional[float] = None,
    jitter: bool = True,
) -> PubFetchResult:
    if opener is None:
        opener = lambda req, timeout: urllib.request.urlopen(req, timeout=timeout)
    if now is None:
        now = time.time()

    publish_dir = provider_publish_dir(pack_root, spec.public_id)
    publish_dir.mkdir(parents=True, exist_ok=True)
    state_path = _state_path(state_dir, pub.name)
    state = _load_state(state_path)

    next_attempt = float(state.get("next_attempt_unix") or 0)
    if next_attempt > now:
        kind = "backoff" if int(state.get("fail_count") or 0) > 0 else "interval_hold"
        return PubFetchResult(
            name=pub.name,
            status=0,
            bytes=0,
            not_modified=False,
            error=f"{kind} until unix={int(next_attempt)}",
            path=publish_dir / f"{pub.name}.xml",
        )

    if jitter:
        span = min(30.0, max(0.0, pub.interval_secs * 0.1))
        if span > 0:
            time.sleep(random.uniform(0.0, span))

    headers: Dict[str, str] = {
        "User-Agent": spec.user_agent,
        "Accept": "application/xml, text/xml, */*",
    }
    if spec.accept_encoding_gzip:
        headers["Accept-Encoding"] = "gzip"
    if pub.use_conditional and pub.fetch_mode == "get":
        lm = state.get("last_modified")
        et = state.get("etag")
        if lm:
            headers["If-Modified-Since"] = lm
        if et:
            headers["If-None-Match"] = et

    try:
        resolved = resolve_publication_url(spec, pub, now=now, day_offset=0)
    except ValueError as exc:
        return PubFetchResult(
            name=pub.name,
            status=0,
            bytes=0,
            not_modified=False,
            error=str(exc),
        )

    try:
        url, headers = apply_auth_headers(spec, auth, headers, resolved)
    except Exception as exc:
        return PubFetchResult(
            name=pub.name,
            status=0,
            bytes=0,
            not_modified=False,
            error=redact_secrets(str(exc), auth),
        )

    safe_url = resolved

    def _http_get(get_url: str, get_headers: Dict[str, str]) -> Tuple[int, bytes, Optional[str], Optional[str]]:
        request = urllib.request.Request(get_url, headers=get_headers, method="GET")
        with opener(request, float(spec.timeout_secs)) as resp:
            status = int(getattr(resp, "status", 200))
            body = resp.read() if status != 304 else b""
            last_mod = None
            etag = None
            if hasattr(resp, "getheader"):
                last_mod = resp.getheader("Last-Modified") or resp.getheader("last-modified")
                etag = resp.getheader("ETag") or resp.getheader("etag")
            # Always decompress gzip magic (NDW *.xml.gz) and Content-Encoding gzip.
            try:
                body = _maybe_gunzip(body)
            except OSError:
                pass
            return status, body, last_mod, etag

    def _fail(status: int, body_len: int, err: str) -> PubFetchResult:
        fail_count = int(state.get("fail_count") or 0) + 1
        delay = min(3600, (2 ** min(fail_count, 8)) * 5)
        state.update(
            {
                "fail_count": fail_count,
                "next_attempt_unix": now + delay,
                "last_status": status,
                "last_error": err[:500],
            }
        )
        _save_state(state_path, state)
        return PubFetchResult(
            name=pub.name,
            status=status,
            bytes=body_len,
            not_modified=False,
            error=f"{err} (backoff {delay}s)",
        )

    last_mod: Optional[str] = None
    etag: Optional[str] = None
    status = 0
    body = b""

    try:
        if pub.fetch_mode == "html_index":
            # Directory listing (TIPI Evenementiel): fetch newest N *.xml and aggregate.
            index_headers = dict(headers)
            index_headers["Accept"] = "text/html, application/xml, */*"
            status, index_body, last_mod, etag = _http_get(url, index_headers)
            if status == 304:
                state["last_status"] = 304
                state["next_attempt_unix"] = now + pub.interval_secs
                state["fail_count"] = 0
                _save_state(state_path, state)
                return PubFetchResult(
                    name=pub.name,
                    status=304,
                    bytes=0,
                    not_modified=True,
                    path=publish_dir / f"{pub.name}.xml",
                )
            if status != 200:
                return _fail(status, len(index_body), f"HTTP {status} for {safe_url}")
            child_urls = list_xml_hrefs_from_html(index_body, safe_url)[: pub.index_max_files]
            if not child_urls:
                return _fail(status, len(index_body), f"no *.xml links in directory {safe_url}")
            parts: List[Tuple[str, bytes]] = []
            child_headers = {
                "User-Agent": spec.user_agent,
                "Accept": "application/xml, text/xml, */*",
            }
            if spec.accept_encoding_gzip:
                child_headers["Accept-Encoding"] = "gzip"
            for child in child_urls:
                try:
                    child_url, ch = apply_auth_headers(spec, auth, dict(child_headers), child)
                    c_status, c_body, _, _ = _http_get(child_url, ch)
                except (urllib.error.URLError, TimeoutError, OSError, urllib.error.HTTPError) as exc:
                    LOG.warning(
                        "provider=%s pub=%s child fetch skip: %s",
                        spec.id,
                        pub.name,
                        redact_secrets(str(exc), auth),
                    )
                    continue
                if c_status != 200 or not c_body:
                    continue
                ok_child, _detail = validate_datex_xml(c_body, spec.datex_version)
                if not ok_child:
                    continue
                parts.append((child.rsplit("/", 1)[-1], c_body))
            if not parts:
                return _fail(status, 0, f"no valid DATEX children under {safe_url}")
            body = build_directory_aggregate(parts, source_url=safe_url)
            # Aggregates use a navi wrapper; validate as DATEX-like without strict version.
            ok, detail = validate_datex_xml(body, "")
        else:
            try:
                status, body, last_mod, etag = _http_get(url, headers)
            except urllib.error.HTTPError as exc:
                status = int(exc.code)
                body = b""
                try:
                    if exc.fp is not None:
                        body = exc.read() or b""
                        try:
                            body = _maybe_gunzip(body)
                        except OSError:
                            pass
                except Exception:
                    body = b""
                # Bulgaria: dated file may roll; retry yesterday once on 404.
                if (
                    status == 404
                    and "{YYYYMMDD}" in pub.url
                    and resolve_publication_url(spec, pub, now=now, day_offset=-1) != resolved
                ):
                    yurl = resolve_publication_url(spec, pub, now=now, day_offset=-1)
                    try:
                        yurl, yheaders = apply_auth_headers(spec, auth, headers, yurl)
                        status, body, last_mod, etag = _http_get(yurl, yheaders)
                        safe_url = yurl
                    except urllib.error.HTTPError as exc2:
                        status = int(exc2.code)
                        return _fail(status, 0, f"HTTP {status} for {yurl}")
                elif status in (401, 403):
                    return PubFetchResult(
                        name=pub.name,
                        status=status,
                        bytes=len(body),
                        not_modified=False,
                        error=f"HTTP {status} for {safe_url}",
                    )
                elif status != 200:
                    return _fail(status, len(body), f"HTTP {status} for {safe_url}")

            if status == 304:
                state["last_status"] = 304
                state["next_attempt_unix"] = now + pub.interval_secs
                state["fail_count"] = 0
                _save_state(state_path, state)
                return PubFetchResult(
                    name=pub.name,
                    status=304,
                    bytes=0,
                    not_modified=True,
                    path=publish_dir / f"{pub.name}.xml",
                )
            if status != 200:
                return _fail(status, len(body), f"HTTP {status} for {safe_url}")
            ok, detail = validate_datex_xml(body, spec.datex_version)

    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        if status in (401, 403):
            return PubFetchResult(
                name=pub.name,
                status=status,
                bytes=0,
                not_modified=False,
                error=f"HTTP {status} for {safe_url}",
            )
        return _fail(status, 0, f"HTTP {status} for {safe_url}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        err = redact_secrets(str(exc), auth)
        return _fail(0, 0, f"network error for {safe_url}: {err}")

    if not ok:
        return _fail(status, len(body), f"DATEX validate failed: {detail}")

    out_path = publish_dir / f"{pub.name}.xml"
    atomic_write_bytes(out_path, body)
    state.update(
        {
            "last_status": status,
            "last_bytes": len(body),
            "last_success_unix": now,
            "fail_count": 0,
            "next_attempt_unix": now + pub.interval_secs,
            "last_error": None,
        }
    )
    if last_mod:
        state["last_modified"] = last_mod
    if etag:
        state["etag"] = etag
    _save_state(state_path, state)
    LOG.info(
        "provider=%s pub=%s status=%s bytes=%s version=%s",
        spec.id,
        pub.name,
        status,
        len(body),
        detail,
    )
    return PubFetchResult(
        name=pub.name,
        status=status,
        bytes=len(body),
        not_modified=False,
        path=out_path,
    )


def write_provider_source_json(
    spec: ProviderSpec,
    pack_root: Path,
    ok_names: List[str],
) -> Path:
    publish_dir = provider_publish_dir(pack_root, spec.public_id)
    meta = {
        "source": spec.source_label,
        "license": spec.licence,
        "attribution": spec.attribution,
        "provider_id": spec.public_id,
        "config_id": spec.id,
        "canonical_base": spec.canonical_base,
        "datex_version": spec.datex_version,
        "country": spec.country,
        "note": (
            "XML files in this directory are unmodified upstream snapshots. "
            "Credentials are not stored here."
        ),
    }
    return write_source_json(publish_dir, meta, endpoints_ok=ok_names)


def write_provider_status(
    status_path: Path,
    *,
    spec: ProviderSpec,
    results: List[PubFetchResult],
    ok: bool,
) -> None:
    status_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "provider_id": spec.id,
        "publish_id": spec.public_id,
        "ok": ok,
        "updated_unix": time.time(),
        "publications": [
            {
                "name": r.name,
                "status": r.status,
                "bytes": r.bytes,
                "not_modified": r.not_modified,
                "error": redact_secrets(r.error or "") or None,
            }
            for r in results
        ],
    }
    partial = status_path.with_suffix(".json.partial")
    partial.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    partial.replace(status_path)
