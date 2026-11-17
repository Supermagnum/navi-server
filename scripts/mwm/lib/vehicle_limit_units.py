"""Parse OSM vehicle-restriction tag values to metres / tonnes.

Plain numeric values only. Conditional suffixes and words such as ``default``,
``none``, ``unsigned``, ``below_default`` yield ``None`` (no limit emitted).
"""
from __future__ import annotations

import re
from typing import Optional

# Words that mean "no enforceable numeric limit" for our sidecar.
_DROP = frozenset(
    {
        "default",
        "none",
        "unsigned",
        "below_default",
        "no",
        "yes",
        "unknown",
        "unlimited",
    }
)

_FT_IN = re.compile(
    r"""^\s*
    (?P<ft>-?\d+(?:\.\d+)?)\s*(?:'|ft|feet)\s*
    (?:(?P<inch>\d+(?:\.\d+)?)\s*(?:"|in|inch|inches)?)?
    \s*$""",
    re.IGNORECASE | re.VERBOSE,
)
_NUM_UNIT = re.compile(
    r"""^\s*
    (?P<num>-?\d+(?:[.,]\d+)?)\s*
    (?P<unit>[a-zA-Z\"']+)?
    \s*$""",
    re.VERBOSE,
)


def _to_float(raw: str) -> Optional[float]:
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def parse_length_metres(raw: str | None) -> Optional[float]:
    """Parse maxheight / maxwidth / maxlength into metres, or None to skip."""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    low = s.lower()
    if low in _DROP or "@" in s or ";" in s:
        return None
    m = _FT_IN.match(s)
    if m:
        ft = _to_float(m.group("ft"))
        if ft is None:
            return None
        inch = _to_float(m.group("inch") or "0") or 0.0
        return ft * 0.3048 + inch * 0.0254
    m = _NUM_UNIT.match(s)
    if not m:
        return None
    num = _to_float(m.group("num"))
    if num is None or num <= 0:
        return None
    unit = (m.group("unit") or "m").lower()
    if unit in ("m", "meter", "meters", "metre", "metres"):
        return num
    if unit in ("cm",):
        return num / 100.0
    if unit in ("mm",):
        return num / 1000.0
    if unit in ("ft", "feet", "'"):
        return num * 0.3048
    if unit in ("in", "inch", "inches", '"'):
        return num * 0.0254
    return None


def parse_weight_tonnes(raw: str | None) -> Optional[float]:
    """Parse maxweight / maxaxleload / maxbogieweight into tonnes, or None."""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    low = s.lower()
    if low in _DROP or "@" in s or ";" in s:
        return None
    m = _NUM_UNIT.match(s)
    if not m:
        return None
    num = _to_float(m.group("num"))
    if num is None or num <= 0:
        return None
    unit = (m.group("unit") or "t").lower()
    if unit in ("t", "ton", "tons", "tonne", "tonnes", "mgw", "mt"):
        return num
    if unit in ("kg",):
        return num / 1000.0
    if unit in ("lb", "lbs", "pound", "pounds"):
        return num * 0.00045359237
    # Bare number on weight tags is conventionally tonnes in OSM.
    if unit in ("",):
        return num
    return None


def metres_to_cm(m: float) -> int:
    """Round metres to centimetres, clamp to u16."""
    cm = int(round(m * 100.0))
    return max(0, min(cm, 65535))


def tonnes_to_10kg(t: float) -> int:
    """Round tonnes to units of 10 kg, clamp to u16."""
    v = int(round(t * 100.0))
    return max(0, min(v, 65535))
