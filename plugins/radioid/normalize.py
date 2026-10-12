"""Normalize RadioID API rows (+ optional map coords) into the Navi schema.

Frequency and offset are MHz in RadioID samples (e.g. frequency=434.88750,
offset=-2.000). Converted to output_hz and signed shift_hz (= offset_MHz * 1e6).

Join key: ``locator`` (see plugins.radioid.JOIN_KEY).
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple

from plugins.repeaterbook.normalize import frequency_to_hz, parse_decimal

from . import (
    ATTRIBUTION_TEXT,
    ATTRIBUTION_URL,
    JOIN_KEY,
    LICENCE_NOTE,
    SCHEMA_VERSION,
    SOURCE_ID,
)


def attribution_block() -> Dict[str, Any]:
    return {
        "text": ATTRIBUTION_TEXT,
        "url": ATTRIBUTION_URL,
        "provisional": True,
    }


def offset_to_shift_hz(raw: Any) -> Optional[int]:
    """RadioID offset is MHz (signed string/number) → Hz."""
    v = parse_decimal(raw)
    if v is None:
        return None
    # Values with |v| < 10000 treated as MHz (same heuristic as frequency).
    if abs(v) < 10000:
        return int(round(v * 1_000_000))
    return int(round(v))


def _active_status(status: Any) -> bool:
    if status is None or status == "":
        return True  # unknown → do not exclude
    s = str(status).strip().lower().replace("_", "-")
    if s in ("active", "on-air", "onair", "on air"):
        return True
    if s in ("inactive", "off-air", "offair", "off air", "retired", "disabled"):
        return False
    return True


def normalize_row(
    row: Mapping[str, Any],
    *,
    map_row: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    callsign = row.get("callsign")
    out_hz = frequency_to_hz(row.get("frequency"))
    shift = offset_to_shift_hz(row.get("offset"))
    color = row.get("color_code")
    color_i: Optional[int] = None
    if color not in (None, ""):
        try:
            color_i = int(float(str(color).replace(",", ".")))
        except ValueError:
            color_i = None

    lat = lon = None
    position_null = True
    if map_row:
        lat = parse_decimal(map_row.get("lat"))
        lon = parse_decimal(map_row.get("lng") or map_row.get("lon"))
        if lat is not None and lon is not None:
            position_null = False

    city = row.get("city")
    state = row.get("state")
    location_parts = [str(p) for p in (city, state) if p not in (None, "")]
    location = ", ".join(location_parts) if location_parts else None

    status = row.get("status")
    exclude = not _active_status(status)

    locator = row.get("locator")
    rb_id = None if locator in (None, "") else str(locator)

    talkgroups = row.get("talkgroups")
    tg_out = None
    if isinstance(talkgroups, list):
        tg_out = []
        for tg in talkgroups:
            if not isinstance(tg, dict):
                continue
            item = {
                k: tg.get(k)
                for k in ("talkgroup", "description", "timeslot", "discovery")
                if tg.get(k) is not None
            }
            if item:
                tg_out.append(item)
        if not tg_out:
            tg_out = None

    network: Dict[str, Any] = {}
    if row.get("ipsc_network") not in (None, ""):
        network["ipsc_network"] = row.get("ipsc_network")
    if row.get("trustee") not in (None, "", []):
        network["trustee"] = row.get("trustee")

    out: Dict[str, Any] = {
        "source": SOURCE_ID,
        "callsign": None if callsign in (None, "") else str(callsign),
        "lat": lat,
        "lon": lon,
        "position": None if position_null else {"lat": lat, "lon": lon},
        "position_null": position_null,
        "coord_source": "radioid_map" if not position_null else None,
        "coord_accuracy": None,
        "output_hz": out_hz,
        "shift_hz": shift,
        "mode": "DMR",
        "dmr_color_code": color_i,
        "talkgroups": tg_out,
        "location": location,
        "network": network or None,
        "operational_status": None if status in (None, "") else str(status),
        "repeaterbook_id": None,  # not from RepeaterBook
        "radioid_locator": rb_id,
        "radioid_identity_id": (
            None if row.get("identity_id") in (None, "") else str(row.get("identity_id"))
        ),
        "updated_at": None,
        "entry_kind": "repeater",
        "exclude_recommended": exclude,
        "join_key": JOIN_KEY,
    }
    keep_always = {
        "source",
        "callsign",
        "lat",
        "lon",
        "position_null",
        "output_hz",
        "shift_hz",
        "entry_kind",
        "exclude_recommended",
        "radioid_locator",
    }
    return {k: v for k, v in out.items() if v is not None or k in keep_always}


def join_and_normalize(
    api_rows: List[Mapping[str, Any]],
    map_by_locator: Mapping[int, Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Join on locator. Stats: matched (with coords), api_only, map_only."""
    api_locs: set[int] = set()
    records: List[Dict[str, Any]] = []
    matched = 0
    api_only = 0
    for row in api_rows:
        loc = row.get("locator")
        try:
            loc_i = int(loc)
        except (TypeError, ValueError):
            api_only += 1
            records.append(normalize_row(row, map_row=None))
            continue
        api_locs.add(loc_i)
        m = map_by_locator.get(loc_i)
        n = normalize_row(row, map_row=m)
        if n.get("position_null"):
            api_only += 1
        else:
            matched += 1
        records.append(n)
    map_only = sum(1 for loc in map_by_locator if loc not in api_locs)
    stats_out = {
        "matched": matched,
        "api_only": api_only,
        "map_only": map_only,
        "join_key": JOIN_KEY,
    }
    return records, stats_out


def envelope(
    *,
    country: str,
    country_slug: str,
    generation: str,
    fetched_unix: int,
    records: List[Dict[str, Any]],
    join_stats: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "source": SOURCE_ID,
        "country": country,
        "country_slug": country_slug,
        "generation": generation,
        "fetched_unix": fetched_unix,
        "record_count": len(records),
        "attribution": attribution_block(),
        "licence": LICENCE_NOTE,
        "join": dict(join_stats),
        "repeaters": records,
    }
