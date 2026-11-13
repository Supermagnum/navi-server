"""Elevation override policy versions and Mapterhorn leaf classification.

Policy v1 (261105 and earlier): uniform 2.0 m threshold for all leaves.
Policy v2 (next CoMaps release default): Copernicus-only leaves whose
coverage is entirely below 60°N use 5.0 m; ≥60°N and all other classes
keep 2.0 m.

Classification mirrors the runtime investigation (`classify_coverage.py`):
prefix rules from Mapterhorn source coverage (national LiDAR / 20 m DTM /
mixed / glo30-only). Regenerate notes live in
`config/mwm/elevation-policy.json`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

POLICY_V1 = 1
POLICY_V2 = 2

DEFAULT_THRESHOLD_M = 2.0
COPERNICUS_LT60_THRESHOLD_M = 5.0
LAT_60N = 60.0

HIGH_RES_PREFIXES = frozenset(
    {
        "Austria",
        "Belgium",
        "Cyprus",
        "Czech",
        "Denmark",
        "Estonia",
        "Faroe Islands",
        "Finland",
        "France",
        "Germany",
        "Iceland",
        "Italy",
        "Luxembourg",
        "Netherlands",
        "Norway",
        "Poland",
        "Portugal",
        "Romania",
        "Sweden",
        "Slovakia",
        "Slovenia",
        "Spain",
        "Switzerland",
        "UK",
        "United Kingdom",
        "US",
        "Japan",
        "New Zealand",
        "Israel",
        "Rwanda",
        "Taiwan",
    }
)

COPERNICUS_ONLY_EXACT = frozenset(
    {
        "France_New Caledonia",
        "France_French Polynesia",
        "France_Wallis and Futuna",
        "Norway_Bouvet Island",
        "Norway_Jan Mayen",
    }
)

MIXED_EXACT = frozenset(
    {
        "Portugal_Islands",
        "Netherlands_Caribisch",
        "Andorra",
        "Liechtenstein",
        "Monaco",
        "Gibraltar",
        "San Marino",
        "Vatican",
    }
)

HIGH_RES_20M_EXACT = frozenset({"Norway_Svalbard"})


def classify_mapterhorn_leaf(leaf: str) -> str:
    """Return high_res | high_res_20m | mixed | copernicus_only."""
    if leaf in COPERNICUS_ONLY_EXACT:
        return "copernicus_only"
    if leaf in MIXED_EXACT:
        return "mixed"
    if leaf in HIGH_RES_20M_EXACT:
        return "high_res_20m"
    if leaf.startswith("Latvia"):
        return "high_res_20m"
    if leaf.startswith("Australia") or leaf.startswith("Canada"):
        return "mixed"
    pref = leaf.split("_", 1)[0]
    if pref in HIGH_RES_PREFIXES or leaf in HIGH_RES_PREFIXES:
        return "high_res"
    if leaf.startswith("US_"):
        return "high_res"
    return "copernicus_only"


def leaf_below_60n(leaf: str, *, lat_max: float | None = None) -> bool:
    """True when the leaf's coverage is entirely south of 60°N.

    Prefer an explicit lat_max from config / dump stats. Fallback heuristics
    cover well-known high-latitude prefixes (northern Scandinavia, Russia,
    Canada, Alaska, Iceland, Greenland, Svalbard, Jan Mayen).
    """
    if lat_max is not None:
        return float(lat_max) < LAT_60N

    # Heuristic: leaves that may touch ≥60°N
    high_lat_markers = (
        "Svalbard",
        "Jan Mayen",
        "Bouvet",
        "Greenland",
        "Iceland",
        "Faroe",
        "Alaska",
        "Yukon",
        "Northwest Territories",
        "Nunavut",
        "Lapland",
        "Norrbotten",
        "Troms",
        "Finnmark",
        "Nordland",
        "Norra Sverige",
        "Northern Finland",
        "Murmansk",
        "Arkhangelsk",
        "Komi",
        "Yamalo",
        "Nenets",
        "Krasnoyarsk",
        "Sakha",
        "Chukotka",
        "Kamchatka",
        "Magadan",
        "Khabarovsk",
        "Primorsky",  # Primorsky is <60 but keep conservative via lat_max when available
    )
    for marker in high_lat_markers:
        if marker in leaf:
            return False
    # Broad northern country prefixes often span ≥60°N
    if leaf.startswith(("Norway_", "Sweden_", "Finland_", "Russia_", "Canada_", "US_Alaska")):
        return False
    return True


def resolve_policy_version(default: int | None = None) -> int:
    raw = os.environ.get("NAVI_MWM_ELEV_POLICY_VERSION", "").strip()
    if raw:
        return int(raw)
    if default is not None:
        return int(default)
    # Default stays v1 until the next CoMaps release opts into v2.
    return POLICY_V1


def threshold_m_for_leaf(
    leaf: str,
    *,
    policy_version: int | None = None,
    lat_max: float | None = None,
    mapterhorn_class: str | None = None,
) -> float:
    ver = resolve_policy_version() if policy_version is None else int(policy_version)
    cls = mapterhorn_class or classify_mapterhorn_leaf(leaf)
    if ver <= POLICY_V1:
        return DEFAULT_THRESHOLD_M
    if cls == "copernicus_only" and leaf_below_60n(leaf, lat_max=lat_max):
        return COPERNICUS_LT60_THRESHOLD_M
    return DEFAULT_THRESHOLD_M


def policy_config_path() -> Path:
    env = os.environ.get("NAVI_MWM_ELEV_POLICY_CONFIG", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "config" / "mwm" / "elevation-policy.json"


def load_policy_config(path: Path | None = None) -> dict[str, Any]:
    p = path or policy_config_path()
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {
            "schema": "navi-mwm-elevation-policy/v1",
            "default_policy_version": POLICY_V1,
            "versions": {
                "1": {"threshold_m_default": 2.0},
                "2": {
                    "threshold_m_default": 2.0,
                    "copernicus_only_below_60n_m": 5.0,
                },
            },
        }
