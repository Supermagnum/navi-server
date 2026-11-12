#!/usr/bin/env python3
"""Build per-release attribution.json + ATTRIBUTION.md for CoMaps-release sidecars."""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

MAPTERHORN_ATTR_URL = "https://download.mapterhorn.com/attribution.json"
DEFAULT_UA = "navi-server-mwm/1.0 (+https://github.com/Supermagnum/navi-server)"

# Operator names where known from CoMaps community / mirror hostnames.
MIRROR_OPERATORS = {
    "cdn-fi-1.comaps.app": "CoMaps (cdn-fi-1)",
    "cdn-us-1.comaps.app": "CoMaps (cdn-us-1 metaserver)",
    "cdn-us-2.comaps.tech": "CoMaps (cdn-us-2)",
    "mapgen-fi-1.comaps.app": "CoMaps mapgen-fi-1",
    "comaps.openstreetmap.fr": "OpenStreetMap France",
    "comaps-it1.unfoxo.it": "unfoxo.it",
    "comaps.firewall-gateway.de": "firewall-gateway.de",
    "comaps-cdn.s3-website.cloud.ru": "Cloud.ru S3 mirror",
}


def fetch_json(url: str) -> dict | list:
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def host_of(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discovery", type=Path, required=True)
    ap.add_argument("--out-json", type=Path, required=True)
    ap.add_argument("--out-md", type=Path, required=True)
    ap.add_argument("--mapterhorn-attribution-url", default=MAPTERHORN_ATTR_URL)
    args = ap.parse_args()

    disc = json.loads(args.discovery.read_text())
    mirrors = []
    for m in disc.get("mirrors") or []:
        host = host_of(m)
        mirrors.append(
            {
                "url": m,
                "host": host,
                "operator": MIRROR_OPERATORS.get(host, host),
            }
        )

    mapterhorn = {"label": "Mapterhorn", "attribution_url": args.mapterhorn_attribution_url}
    try:
        raw = fetch_json(args.mapterhorn_attribution_url)
        if isinstance(raw, dict):
            mapterhorn["sources"] = raw.get("sources") or raw
        else:
            mapterhorn["sources"] = raw
    except Exception as e:  # noqa: BLE001
        mapterhorn["fetch_error"] = str(e)

    body = {
        "schema": "navi-mwm-attribution/v1",
        "mode": "comaps-release",
        "comaps": {
            "map_series": disc.get("map_series"),
            "release_version": disc.get("version"),
            "metaserver_url": disc.get("metaserver_url"),
            "countries_txt_sha256": disc.get("countries_txt_sha256"),
            "copyright": [
                "Map data (c) OpenStreetMap contributors, ODbL",
                "Map packaging and distribution by CoMaps and mirror operators",
            ],
            "mirrors": mirrors,
        },
        "mapterhorn": mapterhorn,
        "navi": {
            "label": "Navi",
            "note": "Wetland Soft/Hard edge costs, boardwalk flags, and Mapterhorn elevation overrides in Navi sidecars",
        },
        "osm": {
            "license": "ODbL",
            "attribution": "© OpenStreetMap contributors",
            "url": "https://www.openstreetmap.org/copyright",
        },
    }
    args.out_json.write_text(json.dumps(body, indent=2) + "\n")

    lines = [
        "# Map data attribution",
        "",
        f"CoMaps release `{disc.get('map_series')}` / `{disc.get('version')}`.",
        "",
        "## OpenStreetMap",
        "",
        "Map data © OpenStreetMap contributors, [ODbL](https://opendatacommons.org/licenses/odbl/).",
        "",
        "## CoMaps and map mirrors",
        "",
        "Base `.mwm` files are produced and distributed by CoMaps. Clients download",
        "them from CoMaps mirrors discovered via the CoMaps metaserver (not from navi-server).",
        "",
    ]
    for m in mirrors:
        lines.append(f"- `{m['host']}` — {m['operator']}")
    lines += [
        "",
        "## Mapterhorn",
        "",
        "Elevation overrides in Navi sidecars sample Mapterhorn Terrarium tiles",
        f"({args.mapterhorn_attribution_url}). Upstream sources are listed in attribution.json.",
        "",
        "## Navi",
        "",
        "Navi adds wetland Soft/Hard edge annotations, boardwalk flags, and elevation",
        "overrides bound to the exact CoMaps MWM fingerprint (size + SHA-256).",
        "",
    ]
    args.out_md.write_text("\n".join(lines) + "\n")
    print(json.dumps({"out_json": str(args.out_json), "mirrors": len(mirrors)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
