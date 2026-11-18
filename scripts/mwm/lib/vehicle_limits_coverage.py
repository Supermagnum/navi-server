"""Per-leaf vehicle_limits coverage metadata for catalog + sidecar VL01 header."""

from __future__ import annotations

from typing import Any


COVERAGE_KEYS = (
    "tagged_ways",
    "matched_ways",
    "match_rate",
    "non_numeric_no_limit",
    "unknown_value",
    "osm_data_date",
    "map_release",
)


def coverage_from_report(
    report: dict[str, Any],
    *,
    osm_data_date: str | None = None,
    map_release: str | None = None,
) -> dict[str, Any]:
    """Build the catalog / sidecar coverage object from an annotate report."""
    tagged = int(report.get("tagged_ways") or 0)
    matched = int(report.get("matched_ways") or 0)
    nn = int(report.get("non_numeric_no_limit") or 0)
    unk = int(report.get("unknown_value") or 0)
    rate = report.get("match_rate")
    if rate is None:
        numeric = int(report.get("numeric_ways") or max(0, tagged - nn - unk - int(report.get("skipped_unparseable_other") or 0)))
        # Fallback when older reports only have skipped_unparseable total.
        if "numeric_ways" not in report:
            skipped = int(report.get("skipped_unparseable") or 0)
            numeric = max(0, tagged - skipped)
        rate = (matched / numeric) if numeric else 0.0
    out = {
        "tagged_ways": tagged,
        "matched_ways": matched,
        "match_rate": float(rate),
        "non_numeric_no_limit": nn,
        "unknown_value": unk,
        "osm_data_date": osm_data_date or report.get("osm_data_date") or "",
        "map_release": map_release or report.get("map_release") or "",
    }
    return out


def coverage_recorded(cov: dict[str, Any] | None) -> bool:
    """True when a leaf has a complete coverage record (gate criterion)."""
    if not isinstance(cov, dict):
        return False
    for k in ("tagged_ways", "matched_ways", "match_rate", "map_release"):
        if k not in cov:
            return False
    if not isinstance(cov["tagged_ways"], int) or cov["tagged_ways"] < 0:
        return False
    if not isinstance(cov["matched_ways"], int) or cov["matched_ways"] < 0:
        return False
    if not isinstance(cov["match_rate"], (int, float)):
        return False
    if not str(cov.get("map_release") or "").strip():
        return False
    return True
