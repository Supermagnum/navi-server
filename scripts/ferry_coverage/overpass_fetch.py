#!/usr/bin/env python3
"""Polite Overpass fetch for route=ferry (+ separate ferry=* without route=ferry).

Saves raw JSON under a scratch directory outside the repo. Skips re-download
when a saved response younger than 24h exists.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = (
    "navi-ferry-coverage/1.0 (+https://github.com/Supermagnum/navi-server; "
    "contact: navi-server ferry coverage analysis)"
)
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
TIMEOUT_S = 1800
MAX_ATTEMPTS = 6

# continent-ish bboxes (south, west, north, east) for fallback splits
CONTINENT_BBOXES: list[tuple[str, float, float, float, float]] = [
    ("africa", -35.0, -20.0, 38.0, 55.0),
    ("europe", 34.0, -25.0, 72.0, 45.0),
    ("asia_west", 10.0, 25.0, 55.0, 100.0),
    ("asia_east", -10.0, 95.0, 55.0, 150.0),
    ("oceania", -50.0, 110.0, 0.0, 180.0),
    ("oceania_west", -50.0, -180.0, 0.0, -120.0),
    ("north_america", 7.0, -170.0, 84.0, -50.0),
    ("south_america", -56.0, -92.0, 13.0, -34.0),
    ("antarctica", -90.0, -180.0, -60.0, 180.0),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _query_route_ferry(bbox: tuple[float, float, float, float] | None = None) -> str:
    # south,west,north,east
    bb = ""
    if bbox is not None:
        s, w, n, e = bbox
        bb = f"({s},{w},{n},{e})"
    return f"""
[out:json][timeout:{TIMEOUT_S}];
(
  way["route"="ferry"]{bb};
  relation["type"="route"]["route"="ferry"]{bb};
);
out tags geom;
""".strip()


def _query_route_ferry_rel_members(
    bbox: tuple[float, float, float, float] | None = None,
) -> str:
    bb = ""
    if bbox is not None:
        s, w, n, e = bbox
        bb = f"({s},{w},{n},{e})"
    # `out;` includes members (needed for parent-relation inheritance). `out tags geom`
    # on relations often yields bounds+tags only.
    return f"""
[out:json][timeout:{TIMEOUT_S}];
relation["type"="route"]["route"="ferry"]{bb};
out;
""".strip()


def _query_ferry_tag_only(bbox: tuple[float, float, float, float] | None = None) -> str:
    bb = ""
    if bbox is not None:
        s, w, n, e = bbox
        bb = f"({s},{w},{n},{e})"
    # ways with ferry=* but not route=ferry
    return f"""
[out:json][timeout:{TIMEOUT_S}];
way["ferry"]["route"!="ferry"]{bb};
out tags geom;
""".strip()


def _post(query: str) -> tuple[dict, bytes]:
    data = query.encode("utf-8")
    req = urllib.request.Request(
        OVERPASS_URL,
        data=data,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    # Overpass expects `data=<query>` form body
    form = b"data=" + urllib.parse.quote(query, safe="").encode("ascii")
    req = urllib.request.Request(
        OVERPASS_URL,
        data=form,
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S + 60) as resp:
        raw = resp.read()
    obj = json.loads(raw.decode("utf-8"))
    return obj, raw


def fetch_with_retry(query: str, label: str) -> tuple[dict, bytes]:
    import urllib.parse  # noqa: F401 — used in _post via local import below

    delay = 20.0
    last_err: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"[{_now_iso()}] {label} attempt {attempt}/{MAX_ATTEMPTS}", flush=True)
            return _post(query)
        except urllib.error.HTTPError as e:
            last_err = e
            code = e.code
            print(f"[{_now_iso()}] HTTP {code} on {label}: {e}", flush=True)
            if code not in (429, 504, 502, 503) and code < 500:
                raise
        except Exception as e:  # noqa: BLE001 — network/timeout
            last_err = e
            print(f"[{_now_iso()}] error on {label}: {e}", flush=True)
        time.sleep(delay)
        delay = min(delay * 2.0, 300.0)
    raise RuntimeError(f"failed {label}: {last_err}")


# Fix _post to import urllib.parse at module level
import urllib.parse  # noqa: E402


def _fresh(path: Path, max_age_h: float = 24.0) -> bool:
    if not path.is_file():
        return False
    age_h = (time.time() - path.stat().st_mtime) / 3600.0
    return age_h < max_age_h


def _merge_elements(parts: list[dict]) -> dict:
    seen: set[tuple[str, int]] = set()
    elements: list[dict] = []
    osm_base = None
    for part in parts:
        rem = part.get("osm3s") or {}
        if rem.get("timestamp_osm_base"):
            osm_base = rem["timestamp_osm_base"]
        for el in part.get("elements") or []:
            key = (el.get("type"), el.get("id"))
            if key in seen:
                continue
            seen.add(key)
            elements.append(el)
    out: dict = {"version": 0.6, "generator": "navi-ferry-coverage merge", "elements": elements}
    if osm_base:
        out["osm3s"] = {"timestamp_osm_base": osm_base}
    return out


def fetch_dataset(
    out_path: Path,
    query_fn,
    label: str,
    meta_path: Path,
) -> dict:
    if _fresh(out_path) and _fresh(meta_path):
        print(f"[{_now_iso()}] skip {label}: fresh cache {out_path}", flush=True)
        return json.loads(out_path.read_text(encoding="utf-8"))

    try:
        obj, raw = fetch_with_retry(query_fn(None), f"{label}/global")
        out_path.write_bytes(raw)
    except Exception as e:  # noqa: BLE001
        print(f"[{_now_iso()}] global {label} failed ({e}); splitting by continent", flush=True)
        parts: list[dict] = []
        for name, s, w, n, ebox in CONTINENT_BBOXES:
            q = query_fn((s, w, n, ebox))
            part, _ = fetch_with_retry(q, f"{label}/{name}")
            parts.append(part)
            time.sleep(5.0)
        obj = _merge_elements(parts)
        out_path.write_text(json.dumps(obj), encoding="utf-8")

    osm_base = (obj.get("osm3s") or {}).get("timestamp_osm_base")
    meta = {
        "label": label,
        "fetched_at_utc": _now_iso(),
        "osm_base": osm_base,
        "element_count": len(obj.get("elements") or []),
        "path": str(out_path),
        "overpass_url": OVERPASS_URL,
        "user_agent": USER_AGENT,
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"[{_now_iso()}] wrote {out_path} elements={meta['element_count']} osm_base={osm_base}", flush=True)
    return obj


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--scratch",
        type=Path,
        required=True,
        help="Scratch dir outside repo (e.g. /tmp/navi-ferry-coverage-YYYYMMDD)",
    )
    args = ap.parse_args()
    scratch = args.scratch
    overpass = scratch / "overpass"
    overpass.mkdir(parents=True, exist_ok=True)

    fetch_dataset(
        overpass / "route_ferry.json",
        _query_route_ferry,
        "route_ferry",
        overpass / "route_ferry.meta.json",
    )
    time.sleep(10.0)
    fetch_dataset(
        overpass / "ferry_tag_only.json",
        _query_ferry_tag_only,
        "ferry_tag_only",
        overpass / "ferry_tag_only.meta.json",
    )
    time.sleep(10.0)
    fetch_dataset(
        overpass / "route_ferry_rel_members.json",
        _query_route_ferry_rel_members,
        "route_ferry_rel_members",
        overpass / "route_ferry_rel_members.meta.json",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
