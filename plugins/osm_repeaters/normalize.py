"""Normalize OSM amateur-radio tags into the Navi /repeaters record shape."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from plugins.repeaterbook.normalize import ctcss_or_dcs, frequency_to_hz, parse_decimal

from . import (
    ATTRIBUTION_TEXT,
    ATTRIBUTION_URL,
    CALLSIGN_KEY,
    LICENCE,
    LICENCE_NOTE,
    LICENCE_URL,
    PREFIX,
    REPEATER_KEY,
    SCHEMA_VERSION,
    SOURCE_ID,
)
from .opl import OplElement

# Emission designator → mode
DESIGNATOR_TO_MODE: Dict[str, str] = {
    "11K2F3E": "FM",
    "16K0F3E": "FM",
    "7K60FXE": "DMR",
    "7K60FXD": "DMR",
    "6K00F7W": "DSTAR",
    "9K36F7W": "C4FM",
    "20K0F2D": "APRS",
    "12K5F1D": "DATA",
}

_DISABLED = frozenset({"no", "false", "0", "disused"})
_MAX_SHIFT_HZ = 100_000_000  # 100 MHz


def attribution_block() -> Dict[str, Any]:
    return {
        "text": ATTRIBUTION_TEXT,
        "url": ATTRIBUTION_URL,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
    }


def split_multi(raw: Optional[str]) -> List[str]:
    if raw is None or raw == "":
        return []
    return [p.strip() for p in str(raw).split(";") if p.strip()]


def _normalize_unit_case(s: str) -> str:
    """Make MHz/Hz unit casing match frequency_to_hz regexes (case-insensitive input)."""
    s = str(s).strip()
    s = re.sub(r"(?i)\s*mhz\s*$", " MHz", s)
    s = re.sub(r"(?i)\s*hz\s*$", " Hz", s)
    return s


def osm_to_hz(raw: Any) -> Optional[int]:
    """OSM freqs: bare number <10000 → MHz, else Hz; unit strings case-insensitive.

    Reuses ``frequency_to_hz`` for unit-bearing strings after normalizing case.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return frequency_to_hz(raw)
    s = _normalize_unit_case(str(raw))
    # Explicit unit → delegate
    if re.search(r"(?i)\b(mhz|hz)\b", s):
        return frequency_to_hz(s)
    # Bare number: wiki vs reality heuristic
    v = parse_decimal(s)
    if v is None:
        return frequency_to_hz(s)
    if abs(v) < 10000:
        return int(round(v * 1_000_000))
    return int(round(v))


def parse_frequency_field(raw: Any, *, stats: Optional[Dict[str, int]] = None) -> Optional[int]:
    """Parse frequency_out / frequency_in; first ;-separated value wins for primary."""
    if raw is None or raw == "":
        return None
    parts = split_multi(str(raw)) if not isinstance(raw, (int, float)) else [raw]
    for part in parts:
        hz = osm_to_hz(part)
        if hz is not None:
            return hz
        if stats is not None:
            stats["unparsed_frequency"] = stats.get("unparsed_frequency", 0) + 1
    return None


def parse_shift_hz(raw: Any, *, stats: Optional[Dict[str, int]] = None) -> Optional[int]:
    if raw is None or raw == "":
        return None
    parts = split_multi(str(raw)) if not isinstance(raw, (int, float)) else [raw]
    for part in parts:
        hz = osm_to_hz(part)
        if hz is None:
            if stats is not None:
                stats["unparsed_shift"] = stats.get("unparsed_shift", 0) + 1
            continue
        if abs(hz) > _MAX_SHIFT_HZ:
            if stats is not None:
                stats["rejected_shift"] = stats.get("rejected_shift", 0) + 1
            continue
        return hz
    return None


def map_designators(raw: Any) -> Tuple[Optional[str], List[str], List[str]]:
    """Return (mode, modes, emission_designators)."""
    parts = split_multi(str(raw) if raw not in (None, "") else "")
    if not parts:
        return None, [], []
    modes: List[str] = []
    for d in parts:
        key = d.upper().replace(" ", "")
        mapped = DESIGNATOR_TO_MODE.get(key)
        if mapped:
            if mapped not in modes:
                modes.append(mapped)
        else:
            if d not in modes:
                modes.append(d)
    mode = modes[0] if modes else None
    return mode, modes, parts


def is_repeater_enabled(tags: Mapping[str, str]) -> Tuple[bool, Optional[str]]:
    """Return (keep, fallback_callsign_from_value)."""
    if REPEATER_KEY not in tags:
        return False, None
    val = (tags.get(REPEATER_KEY) or "").strip()
    low = val.lower()
    if low in _DISABLED:
        return False, None
    if low in ("", "yes", "true", "1"):
        return True, None
    return True, val  # wiki: value may be the callsign


def classify_osm(
    *,
    mode: Optional[str],
    modes: Sequence[str],
    output_hz: Optional[int],
    designators: Sequence[str],
) -> Tuple[str, bool]:
    """APRS digipeaters → entry_kind=aprs, exclude_recommended=true."""
    if "APRS" in modes or (mode or "").upper() == "APRS":
        return "aprs", True
    if any(d.upper().replace(" ", "") == "20K0F2D" for d in designators):
        return "aprs", True
    # Bare 144.800 MHz with no other mapped mode → treat as APRS digipeater.
    if (
        output_hz is not None
        and abs(output_hz - 144_800_000) < 1000
        and not modes
    ):
        return "aprs", True
    return "repeater", False


def _tag(tags: Mapping[str, str], *suffixes: str) -> Optional[str]:
    for s in suffixes:
        k = PREFIX + s if not s.startswith("communication:") else s
        if k in tags and tags[k] not in (None, ""):
            return tags[k]
    return None


def raw_osm_tags(tags: Mapping[str, str]) -> Dict[str, str]:
    keep_keys = {"name", "operator", "ref", "network"}
    out: Dict[str, str] = {}
    for k, v in tags.items():
        if k.startswith("communication:amateur_radio") or k in keep_keys:
            out[k] = v
    return out


def normalize_repeater(
    el: OplElement,
    *,
    lat: Optional[float],
    lon: Optional[float],
    coord_source: Optional[str],
    network_relations: Optional[List[Dict[str, Any]]] = None,
    stats: Optional[Dict[str, int]] = None,
) -> Optional[Dict[str, Any]]:
    keep, fallback_cs = is_repeater_enabled(el.tags)
    if not keep:
        return None
    if el.osm_type == "relation":
        if stats is not None:
            stats["skipped_relation_repeaters"] = stats.get("skipped_relation_repeaters", 0) + 1
        return None

    callsign = el.tags.get(CALLSIGN_KEY) or fallback_cs
    out_hz = parse_frequency_field(_tag(el.tags, "frequency_out"), stats=stats)
    in_hz = parse_frequency_field(_tag(el.tags, "frequency_in"), stats=stats)
    shift = parse_shift_hz(_tag(el.tags, "shift"), stats=stats)
    if shift is None and in_hz is not None and out_hz is not None:
        shift = in_hz - out_hz

    ctcss_raw = _tag(el.tags, "ctcss")
    ctcss_hz, _dcs_from_ctcss = ctcss_or_dcs(
        re.sub(r"(?i)hz$", "", str(ctcss_raw)).strip() if ctcss_raw else None
    )
    toneburst = parse_decimal(_tag(el.tags, "toneburst"))
    dcs = _tag(el.tags, "dcs")
    if dcs is None and _dcs_from_ctcss:
        dcs = _dcs_from_ctcss
    color_raw = _tag(el.tags, "cc")
    color_i: Optional[int] = None
    if color_raw not in (None, ""):
        try:
            color_i = int(float(str(color_raw).replace(",", ".")))
        except ValueError:
            if stats is not None:
                stats["unparsed_cc"] = stats.get("unparsed_cc", 0) + 1
    power = parse_decimal(_tag(el.tags, "power"))
    mode, modes, designators = map_designators(_tag(el.tags, "modulation"))
    entry_kind, exclude = classify_osm(
        mode=mode, modes=modes, output_hz=out_hz, designators=designators
    )

    position_null = lat is None or lon is None
    ts = el.timestamp
    network_block: Dict[str, Any] = {}
    if el.tags.get("network"):
        network_block["name"] = el.tags["network"]
    if network_relations:
        network_block["relations"] = network_relations

    out: Dict[str, Any] = {
        "source": SOURCE_ID,
        "osm_type": el.osm_type,
        "osm_id": el.osm_id,
        "osm_version": el.version,
        "osm_timestamp": ts,
        "updated_at": ts,
        "callsign": None if callsign in (None, "") else str(callsign),
        "name": el.tags.get("name"),
        "lat": lat,
        "lon": lon,
        "position_null": position_null,
        "coord_source": coord_source if not position_null else None,
        "output_hz": out_hz,
        "shift_hz": shift,
        "input_hz": in_hz,
        "ctcss_encode_hz": ctcss_hz,
        "toneburst_hz": float(toneburst) if toneburst is not None else None,
        "dcs_encode": dcs,
        "dmr_color_code": color_i,
        "mode": mode,
        "modes": modes or None,
        "emission_designators": designators or None,
        "power_w": power,
        "operator": el.tags.get("operator"),
        "network": network_block or None,
        "osm_tags": raw_osm_tags(el.tags),
        "entry_kind": entry_kind,
        "exclude_recommended": exclude,
    }
    keep_always = {
        "source",
        "osm_type",
        "osm_id",
        "osm_version",
        "lat",
        "lon",
        "position_null",
        "output_hz",
        "shift_hz",
        "entry_kind",
        "exclude_recommended",
        "updated_at",
    }
    return {k: v for k, v in out.items() if v is not None or k in keep_always}


def network_record(
    el: OplElement,
    *,
    repeater_keys: set,
) -> Optional[Dict[str, Any]]:
    """Build networks[] entry if relation qualifies."""
    if el.osm_type != "relation":
        return None
    if (el.tags.get("type") or "").lower() != "network":
        return None
    has_ham = any(k.startswith("communication:amateur_radio") for k in el.tags)
    members_out = []
    any_in = False
    for mtype, mid, role in el.members:
        in_ds = (mtype, mid) in repeater_keys
        if in_ds:
            any_in = True
        members_out.append(
            {"type": mtype, "id": mid, "role": role or None, "in_dataset": in_ds}
        )
    if not any_in and not has_ham:
        return None
    return {
        "relation_id": el.osm_id,
        "version": el.version,
        "timestamp": el.timestamp,
        "name": el.tags.get("name"),
        "network": el.tags.get("network"),
        "operator": el.tags.get("operator"),
        "tags": dict(el.tags),
        "members": members_out,
    }


def content_sha256(repeaters: Sequence[Mapping[str, Any]], networks: Sequence[Mapping[str, Any]]) -> str:
    import hashlib
    import json

    payload = {"networks": list(networks), "repeaters": list(repeaters)}
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(blob).hexdigest()


def envelope(
    *,
    country: str,
    country_slug: str,
    generation: str,
    fetched_unix: int,
    records: List[Dict[str, Any]],
    networks: List[Dict[str, Any]],
    stats: Mapping[str, Any],
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
        "stats": dict(stats),
        "networks": networks,
        "repeaters": records,
    }
