"""Parse OSM vehicle-restriction tag values to metres / tonnes.

Plain numeric values only. Special words are classified for match-rate
accounting (they never emit a sidecar limit):

* ``none`` / ``default`` / ``unsigned`` / ``no-limit`` (and aliases) →
  ``non_numeric_no_limit`` — exclude from the match-rate denominator.
* ``below_default`` → ``unknown_value`` — exclude from the denominator;
  ways are listed in the leaf report (restriction of unknown size).
"""
from __future__ import annotations

import re
from typing import Literal, Optional

TagValueClass = Literal[
    "numeric",
    "non_numeric_no_limit",
    "unknown_value",
    "unparseable",
    "empty",
]

# Explicit "no enforceable numeric limit" — not a miss for coverage.
_NO_LIMIT = frozenset(
    {
        "default",
        "none",
        "unsigned",
        "no",
        "no-limit",
        "nolimit",
        "unlimited",
    }
)

# Restriction present but magnitude unknown — report, do not match.
_UNKNOWN_VALUE = frozenset(
    {
        "below_default",
    }
)

# Other non-numeric drops (conditionals handled separately via @ / ;).
_OTHER_DROP = frozenset(
    {
        "yes",
        "unknown",
        "fixme",
        "null",
        "nan",
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


def classify_tag_value(raw: str | None) -> TagValueClass:
    """Classify one raw OSM tag value for match-rate accounting."""
    if raw is None:
        return "empty"
    s = str(raw).strip()
    if not s:
        return "empty"
    low = s.lower()
    if low in _NO_LIMIT:
        return "non_numeric_no_limit"
    if low in _UNKNOWN_VALUE:
        return "unknown_value"
    if low in _OTHER_DROP or "@" in s or ";" in s:
        return "unparseable"
    if _try_parse_length(s) is not None or _try_parse_weight(s) is not None:
        return "numeric"
    return "unparseable"


def _try_parse_length(s: str) -> Optional[float]:
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


def _try_parse_weight(s: str) -> Optional[float]:
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
    if unit in ("",):
        return num
    return None


def parse_length_metres(raw: str | None) -> Optional[float]:
    """Parse maxheight / maxwidth / maxlength into metres, or None to skip."""
    if classify_tag_value(raw) != "numeric":
        return None
    assert raw is not None
    return _try_parse_length(str(raw).strip())


def parse_weight_tonnes(raw: str | None) -> Optional[float]:
    """Parse maxweight / maxaxleload / maxbogieweight into tonnes, or None."""
    if classify_tag_value(raw) != "numeric":
        return None
    assert raw is not None
    return _try_parse_weight(str(raw).strip())


def metres_to_cm(m: float) -> int:
    """Round metres to centimetres, clamp to u16."""
    cm = int(round(m * 100.0))
    return max(0, min(cm, 65535))


def tonnes_to_10kg(t: float) -> int:
    """Round tonnes to units of 10 kg, clamp to u16."""
    v = int(round(t * 100.0))
    return max(0, min(v, 65535))
