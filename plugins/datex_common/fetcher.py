"""Generic DATEX publication fetcher (all auth types). Never logs secrets."""

from __future__ import annotations

import gzip
import json
import logging
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .auth_types import AuthMaterial, apply_auth_headers, redact_secrets
from .provider_spec import ProviderSpec, Publication
from .publish import atomic_write_bytes, provider_publish_dir, write_source_json

LOG = logging.getLogger("navi.datex.fetcher")

UrlOpener = Callable[[urllib.request.Request, float], object]


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
    head = xml_bytes[:4096].decode("utf-8", errors="replace")
    if "datex2.eu/schema/3/" in head or "messageContainer" in head:
        return "3.x"
    if "datex2.eu/schema/2/" in head or "d2LogicalModel" in head:
        return "2.x"
    return "unknown"


def validate_datex_xml(xml_bytes: bytes, expect_version: str) -> tuple[bool, str]:
    if not xml_bytes or b"<" not in xml_bytes[:500]:
        return False, "not XML"
    detected = detect_datex_version(xml_bytes)
    exp = (expect_version or "").strip()
    if exp.startswith("3") and not detected.startswith("3"):
        return False, f"expected DATEX 3.x, detected {detected}"
    if exp.startswith("2") and not detected.startswith("2"):
        return False, f"expected DATEX 2.x, detected {detected}"
    return True, detected


def _maybe_gunzip(body: bytes) -> bytes:
    if len(body) >= 2 and body[0] == 0x1F and body[1] == 0x8B:
        return gzip.decompress(body)
    return body


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
    if pub.use_conditional:
        lm = state.get("last_modified")
        et = state.get("etag")
        if lm:
            headers["If-Modified-Since"] = lm
        if et:
            headers["If-None-Match"] = et

    try:
        url, headers = apply_auth_headers(spec, auth, headers, pub.url)
    except Exception as exc:
        return PubFetchResult(
            name=pub.name,
            status=0,
            bytes=0,
            not_modified=False,
            error=redact_secrets(str(exc), auth),
        )

    request = urllib.request.Request(url, headers=headers, method="GET")
    safe_url = pub.url  # original without query secrets when possible

    try:
        with opener(request, float(spec.timeout_secs)) as resp:
            status = int(getattr(resp, "status", 200))
            body = resp.read() if status != 304 else b""
            last_mod = None
            etag = None
            if hasattr(resp, "getheader"):
                last_mod = resp.getheader("Last-Modified") or resp.getheader("last-modified")
                etag = resp.getheader("ETag") or resp.getheader("etag")
                enc = (resp.getheader("Content-Encoding") or "").lower()
            else:
                enc = ""
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
            if "gzip" in enc or spec.accept_encoding_gzip:
                try:
                    body = _maybe_gunzip(body)
                except OSError:
                    pass
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        body = b""
        try:
            if exc.fp is not None:
                body = exc.read() or b""
        except Exception:
            body = b""
        if status in (401, 403):
            msg = f"HTTP {status} for {safe_url}"
            return PubFetchResult(
                name=pub.name,
                status=status,
                bytes=len(body),
                not_modified=False,
                error=msg,
            )
        fail_count = int(state.get("fail_count") or 0) + 1
        delay = min(3600, (2 ** min(fail_count, 8)) * 5)
        state.update(
            {
                "fail_count": fail_count,
                "next_attempt_unix": now + delay,
                "last_status": status,
                "last_error": f"HTTP {status}",
            }
        )
        _save_state(state_path, state)
        return PubFetchResult(
            name=pub.name,
            status=status,
            bytes=len(body),
            not_modified=False,
            error=f"HTTP {status} for {safe_url} (backoff {delay}s)",
        )
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        fail_count = int(state.get("fail_count") or 0) + 1
        delay = min(3600, (2 ** min(fail_count, 8)) * 5)
        err = redact_secrets(str(exc), auth)
        state.update(
            {
                "fail_count": fail_count,
                "next_attempt_unix": now + delay,
                "last_status": 0,
                "last_error": err,
            }
        )
        _save_state(state_path, state)
        return PubFetchResult(
            name=pub.name,
            status=0,
            bytes=0,
            not_modified=False,
            error=f"network error for {safe_url}: {err} (backoff {delay}s)",
        )

    ok, detail = validate_datex_xml(body, spec.datex_version)
    if not ok:
        fail_count = int(state.get("fail_count") or 0) + 1
        delay = min(3600, (2 ** min(fail_count, 8)) * 5)
        state.update(
            {
                "fail_count": fail_count,
                "next_attempt_unix": now + delay,
                "last_status": status,
                "last_error": detail,
            }
        )
        _save_state(state_path, state)
        return PubFetchResult(
            name=pub.name,
            status=status,
            bytes=len(body),
            not_modified=False,
            error=f"DATEX validate failed: {detail}",
        )

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
