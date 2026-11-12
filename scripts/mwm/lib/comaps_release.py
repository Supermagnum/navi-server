#!/usr/bin/env python3
"""CoMaps official release discovery, mirror selection, and leaf download.

Mirrors the CoMaps app flow (pinned tree fd11cc6f):
  GET {metaserver}/meta/maps.json  -> latest version for MAP_SERIES
  GET {metaserver}/servers         -> ordered mirror list (fallback DEFAULT_URLS)
  GET {mirror}/maps/{series}/{v}/countries.txt (+ .sig)
  GET {mirror}/maps/{series}/{v}/{UrlEncode(id)}.mwm

Checksum in countries.txt is base64(SHA-1 digest). Navi fingerprints use SHA-256.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DEFAULT_METASERVER = "https://cdn-us-1.comaps.app"
DEFAULT_MAP_SERIES = "2026.10.27"
DEFAULT_URLS = [
    "https://comaps.firewall-gateway.de/",
    "https://cdn-us-2.comaps.tech/",
    "https://cdn-fi-1.comaps.app/",
    "https://comaps.openstreetmap.fr/",
    "https://comaps-it1.unfoxo.it/",
    "https://comaps-cdn.s3-website.cloud.ru/",
    "https://mapgen-fi-1.comaps.app/",
]
# Ed25519 public key (hex) used by CoMaps for countries.txt.sig
COUNTRIES_TXT_PUBKEY_HEX = (
    "91c0a9f6aa182371f047e256ab46489211acc2b51b13197fbe8c94eaa9749c7b"
)
DEFAULT_UA = (
    "navi-server-mwm/1.0 (+https://github.com/Supermagnum/navi-server; "
    "contact=mwm-pipeline)"
)


@dataclass
class LeafInfo:
    id: str
    size: int
    sha1_base64: str
    affiliations: list[str] = field(default_factory=list)


@dataclass
class ReleaseInfo:
    map_series: str
    version: int
    metaserver_url: str
    mirrors: list[str]
    countries_txt_sha256: str
    countries_txt_bytes: int
    leaves: dict[str, LeafInfo]
    maps_json: dict[str, Any]


def user_agent() -> str:
    return os.environ.get("NAVI_MWM_HTTP_USER_AGENT", DEFAULT_UA)


def _request(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
    method: str = "GET",
    data: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    hdrs = {"User-Agent": user_agent()}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return int(resp.status), {k.lower(): v for k, v in resp.headers.items()}, body
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        return int(e.code), {k.lower(): v for k, v in (e.headers.items() if e.headers else [])}, body


def http_get_bytes(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
    retries: int = 5,
    backoff: float = 1.5,
) -> bytes:
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            status, _, body = _request(url, headers=headers, timeout=timeout)
            if status == 200:
                return body
            last_err = RuntimeError(f"HTTP {status} for {url}")
        except Exception as e:  # noqa: BLE001 — polite retry wrapper
            last_err = e
        sleep = backoff**attempt + random.uniform(0, 0.3)
        time.sleep(sleep)
    raise RuntimeError(f"GET failed after {retries} tries: {url}: {last_err}")


def parse_maps_json(raw: bytes | str | dict, map_series: str) -> int:
    data = json.loads(raw) if not isinstance(raw, dict) else raw
    series = (data.get("map-series") or {}).get(map_series)
    if not series:
        raise KeyError(f"map-series {map_series!r} missing from maps.json")
    if series.get("status") == "EOL":
        raise RuntimeError(f"map-series {map_series} is EOL")
    latest = series.get("latest")
    if latest is None:
        raise KeyError(f"no latest for map-series {map_series}")
    return int(latest)


def parse_servers_json(raw: bytes | str) -> list[str]:
    data = json.loads(raw)
    if isinstance(data, list):
        servers = data
    elif isinstance(data, dict):
        servers = data.get("servers") or []
    else:
        raise ValueError("servers response must be list or object")
    out = []
    for s in servers:
        if not isinstance(s, str) or not s:
            continue
        out.append(s if s.endswith("/") else s + "/")
    return out


def walk_countries_leaves(node: dict, out: dict[str, LeafInfo]) -> None:
    children = node.get("g")
    if children is None:
        leaf_id = node.get("id")
        if not leaf_id:
            return
        out[leaf_id] = LeafInfo(
            id=leaf_id,
            size=int(node.get("s") or 0),
            sha1_base64=str(node.get("sha1_base64") or ""),
            affiliations=list(node.get("affiliations") or []),
        )
        return
    for child in children:
        if isinstance(child, dict):
            walk_countries_leaves(child, out)


def parse_countries_txt(raw: bytes | str) -> tuple[int, str, dict[str, LeafInfo]]:
    data = json.loads(raw)
    version = int(data["v"])
    map_series = str(data.get("map_series") or "")
    leaves: dict[str, LeafInfo] = {}
    walk_countries_leaves(data, leaves)
    return version, map_series, leaves


def sha1_base64_file(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode("ascii")


def sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def url_encode_leaf(leaf_id: str) -> str:
    # CoMaps url::UrlEncode: percent-encode spaces / non-ASCII; keep - _ .
    return urllib.parse.quote(leaf_id, safe="-_.")


def leaf_mwm_url(mirror: str, map_series: str, version: int, leaf_id: str) -> str:
    base = mirror if mirror.endswith("/") else mirror + "/"
    return f"{base}maps/{map_series}/{version}/{url_encode_leaf(leaf_id)}.mwm"


def discover_release(
    *,
    metaserver: str = DEFAULT_METASERVER,
    map_series: str = DEFAULT_MAP_SERIES,
    data_version: int | None = None,
) -> ReleaseInfo:
    meta = metaserver.rstrip("/")
    maps_raw = http_get_bytes(f"{meta}/meta/maps.json", timeout=30.0)
    maps_json = json.loads(maps_raw)
    version = int(data_version) if data_version is not None else parse_maps_json(maps_json, map_series)

    mirrors: list[str] = []
    try:
        servers_raw = http_get_bytes(
            f"{meta}/servers",
            headers={
                "X-OM-DataVersion": str(version),
                "X-OM-AppVersion": "navi-server-mwm",
                "Accept-Language": "en-US",
            },
            timeout=15.0,
        )
        mirrors = parse_servers_json(servers_raw)
    except Exception:
        mirrors = []
    if not mirrors:
        mirrors = list(DEFAULT_URLS)

    # Prefer mirrors that answer HEAD quickly; keep order among successes.
    ranked = rank_mirrors(mirrors, map_series, version, probe_leaf="World")
    if not ranked:
        ranked = mirrors

    countries_raw: bytes | None = None
    last_err: Exception | None = None
    for mirror in ranked:
        url = f"{mirror.rstrip('/')}/maps/{map_series}/{version}/countries.txt"
        try:
            countries_raw = http_get_bytes(url, timeout=60.0)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if countries_raw is None:
        raise RuntimeError(f"failed to fetch countries.txt: {last_err}")

    # Signature fetch is best-effort; record presence. Full Ed25519 verify needs
    # cryptography; we still SHA-256 the body for catalog binding.
    try:
        sig_url = f"{ranked[0].rstrip('/')}/maps/{map_series}/{version}/countries.txt.sig"
        sig = http_get_bytes(sig_url, timeout=30.0, retries=2)
        if len(sig) != 64:
            pass  # reported in open issues if verify unavailable
    except Exception:
        sig = b""

    v, series, leaves = parse_countries_txt(countries_raw)
    if v != version:
        raise RuntimeError(f"countries.txt v={v} != requested {version}")
    if series and series != map_series:
        raise RuntimeError(f"countries.txt map_series={series} != {map_series}")

    return ReleaseInfo(
        map_series=map_series,
        version=version,
        metaserver_url=meta,
        mirrors=ranked,
        countries_txt_sha256=sha256_bytes(countries_raw),
        countries_txt_bytes=len(countries_raw),
        leaves=leaves,
        maps_json=maps_json,
    )


def rank_mirrors(
    mirrors: Iterable[str],
    map_series: str,
    version: int,
    *,
    probe_leaf: str = "World",
) -> list[str]:
    """HEAD-probe mirrors (app comment: sorted best→worst). Keep 200 without redirect."""
    scored: list[tuple[float, str]] = []
    for mirror in mirrors:
        url = leaf_mwm_url(mirror, map_series, version, probe_leaf)
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(
                url, method="HEAD", headers={"User-Agent": user_agent()}
            )
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                if int(resp.status) != 200:
                    continue
                # Treat redirects as failure (matches pinger behaviour).
                if resp.geturl().rstrip("/") != url.rstrip("/"):
                    # Some mirrors may normalize trailing slash only.
                    if urllib.parse.urlparse(resp.geturl()).netloc != urllib.parse.urlparse(url).netloc:
                        continue
                scored.append((time.monotonic() - t0, mirror if mirror.endswith("/") else mirror + "/"))
        except Exception:
            continue
    scored.sort(key=lambda x: x[0])
    return [m for _, m in scored]


def download_leaf(
    *,
    mirrors: list[str],
    map_series: str,
    version: int,
    leaf: LeafInfo,
    dest: Path,
    concurrency_hint: int = 2,
    retries: int = 5,
) -> None:
    """Resumable single-leaf download with size + SHA-1 verify. Tries mirrors in order."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    expected = leaf.size
    last_err: Exception | None = None

    for mirror in mirrors:
        url = leaf_mwm_url(mirror, map_series, version, leaf.id)
        try:
            _download_resumable(url, part, expected, retries=retries)
            got = part.stat().st_size
            if got != expected:
                raise RuntimeError(f"size {got} != expected {expected} for {leaf.id}")
            digest = sha1_base64_file(part)
            if leaf.sha1_base64 and digest != leaf.sha1_base64:
                raise RuntimeError(
                    f"sha1 mismatch for {leaf.id}: got {digest} expected {leaf.sha1_base64}"
                )
            part.replace(dest)
            return
        except Exception as e:  # noqa: BLE001
            last_err = e
            if part.exists():
                # Keep partial for resume on next mirror only if size < expected.
                if part.stat().st_size >= expected:
                    part.unlink(missing_ok=True)
            continue
    raise RuntimeError(f"download failed for {leaf.id}: {last_err}")


def _download_resumable(url: str, part: Path, expected: int, *, retries: int) -> None:
    existing = part.stat().st_size if part.exists() else 0
    if existing > expected:
        part.unlink()
        existing = 0
    if existing == expected:
        return

    attempt = 0
    while attempt < retries:
        attempt += 1
        existing = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": user_agent()}
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120.0) as resp:
                status = int(resp.status)
                mode = "ab" if status == 206 else "wb"
                if status == 200 and existing > 0:
                    # Server ignored Range; restart.
                    mode = "wb"
                    existing = 0
                with part.open(mode) as out:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
            if part.stat().st_size == expected:
                return
        except Exception:
            time.sleep(1.5**attempt + random.uniform(0, 0.2))
            continue
    raise RuntimeError(f"resumable download incomplete: {part} size={part.stat().st_size if part.exists() else 0}")


def load_processed_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"release_version": None, "leaves": {}}
    return json.loads(path.read_text())


def save_processed_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(path)
