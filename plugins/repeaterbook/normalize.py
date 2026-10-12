"""Normalize RepeaterBook export rows into the Navi CAT repeater schema.

Missing upstream fields stay missing (omitted or null). Never invent values.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import ATTRIBUTION_TEXT, ATTRIBUTION_URL, SCHEMA_VERSION

_MHZ_RE = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*(?:MHz|mhz|MHZ)?\s*$")
_HZ_RE = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*(?:Hz|hz|HZ)\s*$")
_NUM_RE = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*$")


def _get(row: Mapping[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    # Case-insensitive fallback
    lower = {str(k).lower(): v for k, v in row.items()}
    for k in keys:
        v = lower.get(k.lower())
        if v not in (None, ""):
            return v
    return None


def parse_decimal(raw: Any) -> Optional[float]:
    """Parse numbers that may use a decimal comma. Returns None if missing/invalid."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace(" ", "")
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def frequency_to_hz(raw: Any) -> Optional[int]:
    """Convert MHz (default) or explicit Hz strings to integer Hz."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        v = float(raw)
        # Heuristic: values < 10000 are MHz; larger are already Hz.
        if abs(v) < 10000:
            return int(round(v * 1_000_000))
        return int(round(v))
    s = str(raw).strip()
    if not s:
        return None
    m = _HZ_RE.match(s.replace(",", "."))
    if m:
        return int(round(float(m.group(1).replace(",", "."))))
    m = _MHZ_RE.match(s) or _NUM_RE.match(s.replace(",", "."))
    if not m:
        # Try stripping units loosely
        cleaned = s.replace(",", ".")
        cleaned = re.sub(r"[^\d.\-]", "", cleaned)
        if not cleaned or cleaned in (".", "-", "-."):
            return None
        try:
            v = float(cleaned)
        except ValueError:
            return None
        if abs(v) < 10000:
            return int(round(v * 1_000_000))
        return int(round(v))
    v = float(m.group(1).replace(",", "."))
    if _HZ_RE.match(s.replace(",", ".")):
        return int(round(v))
    return int(round(v * 1_000_000))


def ctcss_or_dcs(raw: Any) -> Tuple[Optional[float], Optional[str]]:
    """Return (ctcss_hz, dcs_code). DCS codes stay as strings; CTCSS as Hz float."""
    if raw is None or raw == "":
        return None, None
    s = str(raw).strip()
    if not s:
        return None, None
    upper = s.upper()
    if upper in ("CSQ", "NONE", "OFF", "N/A", "NA", "-"):
        return None, None
    if upper.startswith("D") or "DCS" in upper or upper.endswith("N") or upper.endswith("I"):
        # DCS tone codes like D023N / 023N
        code = upper.replace("DCS", "").strip()
        return None, code or upper
    hz = parse_decimal(s)
    if hz is None:
        return None, s
    # CTCSS is usually 67–254.1 Hz
    if 50 <= hz <= 300:
        return hz, None
    return hz, None


def _truthy_flag(raw: Any) -> Optional[bool]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return None


def classify_entry(row: Mapping[str, Any]) -> Tuple[str, bool]:
    """Return (entry_kind, exclude_recommended).

    APRS / simplex are flagged so clients can exclude them. When RepeaterBook
    does not mark the row, kind stays ``repeater``.
    """
    mode = str(_get(row, "Mode", "Operating Mode", "mode") or "").lower()
    use = str(_get(row, "Use", "use", "Service Type", "stype") or "").lower()
    notes = str(_get(row, "Notes", "notes") or "").lower()
    landmark = str(_get(row, "Landmark", "landmark") or "").lower()
    blob = " ".join((mode, use, notes, landmark))

    if "aprs" in blob:
        return "aprs", True
    if "simplex" in blob:
        return "simplex", True
    # Zero offset + identical input/output often means simplex; only flag when
    # explicit, never invent — skip heuristic invention.
    return "repeater", False


def shift_hz(output_hz: Optional[int], input_hz: Optional[int]) -> Optional[int]:
    """Signed shift: input − output (positive = +offset / negative split)."""
    if output_hz is None or input_hz is None:
        return None
    return int(input_hz - output_hz)


def normalize_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    callsign = _get(row, "Callsign", "callsign")
    lat = parse_decimal(_get(row, "Lat", "Latitude", "lat", "latitude"))
    lon = parse_decimal(_get(row, "Long", "Lon", "Longitude", "lng", "longitude"))
    out_hz = frequency_to_hz(_get(row, "Frequency", "frequency", "Output Frequency", "freq"))
    in_hz = frequency_to_hz(
        _get(row, "Input Freq", "Input Frequency", "input_freq", "input_frequency", "Input")
    )
    pl_ctcss, pl_dcs = ctcss_or_dcs(_get(row, "PL", "PL Uplink", "CTCSS", "Uplink Tone", "tone"))
    tsq_ctcss, tsq_dcs = ctcss_or_dcs(
        _get(row, "TSQ", "TSQ Downlink", "CTCSS Downlink", "Downlink Tone", "tone_sql")
    )
    mode = _get(row, "Mode", "Operating Mode", "mode")
    color = _get(row, "DMR Color Code", "Color Code", "dmr_color_code", "CC")
    color_i: Optional[int] = None
    if color is not None and color != "":
        try:
            color_i = int(float(str(color).replace(",", ".")))
        except ValueError:
            color_i = None

    city = _get(row, "Nearest City", "City", "city", "Location")
    landmark = _get(row, "Landmark", "landmark")
    location_parts = [str(p) for p in (city, landmark) if p not in (None, "")]
    location = ", ".join(location_parts) if location_parts else None

    status = _get(row, "Operational Status", "Status", "operational_status", "status")
    rb_id = _get(row, "Rptr ID", "Repeater ID", "repeater_id", "id", "State ID")
    # Prefer composite when both present
    state_id = _get(row, "State ID", "state_id")
    rptr_id = _get(row, "Rptr ID", "Repeater ID", "repeater_id")
    if state_id not in (None, "") and rptr_id not in (None, ""):
        rb_id = f"{state_id}:{rptr_id}"
    elif rptr_id not in (None, ""):
        rb_id = rptr_id

    updated = _get(row, "Last Update", "last_update", "Updated", "updated_at")
    coord_source = _get(row, "Precise", "coord_source", "Coordinate Source")
    # Precise is often Yes/No in exports — map to a source hint only when present.
    if isinstance(coord_source, str) and coord_source.lower() in ("yes", "y", "1", "true"):
        coord_source = "repeaterbook_precise"
    elif isinstance(coord_source, str) and coord_source.lower() in ("no", "n", "0", "false"):
        coord_source = "repeaterbook"

    kind, exclude = classify_entry(row)

    network: Dict[str, Any] = {}
    for label, keys in (
        ("allstar", ("AllStar Node", "AllStar", "allstar")),
        ("echolink", ("EchoLink Node", "EchoLink", "echolink")),
        ("irlp", ("IRLP Node", "IRLP", "irlp")),
        ("wires", ("WIRES Node", "WIRES", "wires")),
        ("dmr_id", ("DMR ID", "dmr_id")),
    ):
        val = _get(row, *keys)
        if val not in (None, "", "0", 0):
            network[label] = val

    out: Dict[str, Any] = {
        "callsign": None if callsign in (None, "") else str(callsign),
        "lat": lat,
        "lon": lon,
        "coord_source": coord_source if coord_source not in (None, "") else None,
        "coord_accuracy": None,  # not inventing; upstream rarely gives metres
        "output_hz": out_hz,
        "shift_hz": shift_hz(out_hz, in_hz),
        "ctcss_encode_hz": pl_ctcss,
        "ctcss_decode_hz": tsq_ctcss,
        "dcs_encode": pl_dcs,
        "dcs_decode": tsq_dcs,
        "mode": None if mode in (None, "") else str(mode),
        "dmr_color_code": color_i,
        "location": location,
        "network": network or None,
        "operational_status": None if status in (None, "") else str(status),
        "repeaterbook_id": None if rb_id in (None, "") else str(rb_id),
        "updated_at": None if updated in (None, "") else str(updated),
        "entry_kind": kind,
        "exclude_recommended": exclude,
    }
    # Drop keys that are entirely missing (None) except booleans / required shape.
    keep_always = {
        "callsign",
        "lat",
        "lon",
        "output_hz",
        "shift_hz",
        "entry_kind",
        "exclude_recommended",
        "repeaterbook_id",
    }
    return {k: v for k, v in out.items() if v is not None or k in keep_always}


def normalize_rows(rows: List[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [normalize_row(r) for r in rows]


def attribution_block() -> Dict[str, str]:
    return {
        "text": ATTRIBUTION_TEXT,
        "url": ATTRIBUTION_URL,
    }


def envelope(
    *,
    country: str,
    country_slug: str,
    generation: str,
    fetched_unix: int,
    records: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "country": country,
        "country_slug": country_slug,
        "generation": generation,
        "fetched_unix": fetched_unix,
        "record_count": len(records),
        "attribution": attribution_block(),
        "repeaters": records,
    }
