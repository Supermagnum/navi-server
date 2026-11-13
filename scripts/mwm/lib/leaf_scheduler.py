#!/usr/bin/env python3
"""Parallel leaf scheduling for CoMaps-release bake.

Downloads stay at polite concurrency (default 2). Processing concurrency is
separate and defaults from free RAM: per-leaf peak from measured-peaks.json
(8.2 GiB gates2), leave ≥16 GiB headroom. On media (~40+ GiB MemAvailable)
that yields concurrency 3. Override with NAVI_MWM_PROCESS_CONCURRENCY.

Order: Nordic first, then remaining Europe, then World / rest.
Resume via processed.json (atomic write). Safe under systemd Restart=on-failure
when the outer bake holds pack-pipeline.lock for the whole run.
Never start while the weekly .rkyv bake holds pack-pipeline.lock (flock).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

# Measured comaps-release leaf process peak RSS (gates2 Nordic /usr/bin/time -v).
DEFAULT_PER_LEAF_GIB = 8.2
DEFAULT_HEADROOM_GIB = 16.0

NORDIC_PREFIXES = (
    "Denmark_",
    "Finland_",
    "Norway_",
    "Sweden_",
)

EUROPE_HINTS = (
    "Albania",
    "Andorra",
    "Austria",
    "Belarus",
    "Belgium",
    "Bosnia",
    "Bulgaria",
    "Croatia",
    "Cyprus",
    "Czech",
    "Denmark",
    "Estonia",
    "Faroe",
    "Finland",
    "France",
    "Germany",
    "Gibraltar",
    "Greece",
    "Hungary",
    "Iceland",
    "Ireland",
    "Isle of Man",
    "Italy",
    "Kosovo",
    "Latvia",
    "Liechtenstein",
    "Lithuania",
    "Luxembourg",
    "Malta",
    "Moldova",
    "Monaco",
    "Montenegro",
    "Netherlands",
    "North Macedonia",
    "Norway",
    "Poland",
    "Portugal",
    "Romania",
    "Russia_",  # European Russia leaves still under Europe bucket for ordering
    "San Marino",
    "Serbia",
    "Slovakia",
    "Slovenia",
    "Spain",
    "Sweden",
    "Switzerland",
    "Ukraine",
    "United Kingdom",
    "Vatican",
)


def available_ram_gib() -> float:
    """Prefer MemAvailable from /proc/meminfo (GiB)."""
    try:
        text = Path("/proc/meminfo").read_text()
    except OSError:
        return float(os.environ.get("NAVI_MWM_ASSUME_RAM_GIB", "32"))
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            kb = float(line.split()[1])
            return kb / (1024.0 * 1024.0)
    return float(os.environ.get("NAVI_MWM_ASSUME_RAM_GIB", "32"))


def default_process_concurrency(
    *,
    per_leaf_gib: float = DEFAULT_PER_LEAF_GIB,
    headroom_gib: float = DEFAULT_HEADROOM_GIB,
    available_gib: float | None = None,
) -> int:
    """floor((available - headroom) / per_leaf), at least 1."""
    avail = available_ram_gib() if available_gib is None else available_gib
    usable = avail - headroom_gib
    if usable < per_leaf_gib:
        return 1
    return max(1, int(usable // per_leaf_gib))


def _load_measured_per_leaf_gib() -> float:
    path = os.environ.get("NAVI_MWM_MEASURED_PEAKS", "").strip()
    if not path:
        root = Path(__file__).resolve().parents[3]
        path = str(root / "config" / "mwm" / "measured-peaks.json")
    try:
        data = json.loads(Path(path).read_text())
        v = data.get("comaps_release_process_peak_rss_gib")
        if v is not None:
            return float(v)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return DEFAULT_PER_LEAF_GIB


def resolve_process_concurrency() -> int:
    """Explicit override wins; 0 / empty / unset → derive from RAM + measured peak."""
    raw = os.environ.get("NAVI_MWM_PROCESS_CONCURRENCY", "").strip()
    if raw and raw != "0":
        return max(1, int(raw))
    per = float(os.environ.get("NAVI_MWM_PER_LEAF_GIB", str(_load_measured_per_leaf_gib())))
    head = float(os.environ.get("NAVI_MWM_RAM_HEADROOM_GIB", str(DEFAULT_HEADROOM_GIB)))
    return default_process_concurrency(per_leaf_gib=per, headroom_gib=head)


def resolve_download_concurrency() -> int:
    return max(1, int(os.environ.get("NAVI_MWM_DOWNLOAD_CONCURRENCY", "2")))


def is_nordic(leaf_id: str) -> bool:
    return leaf_id.startswith(NORDIC_PREFIXES)


def is_europe(leaf_id: str) -> bool:
    if is_nordic(leaf_id):
        return True
    if leaf_id == "World" or leaf_id.startswith("World_"):
        return False
    for hint in EUROPE_HINTS:
        if leaf_id.startswith(hint) or hint in leaf_id:
            return True
    # CoMaps Europe_* composite ids
    if leaf_id.startswith("Europe") or "_Europe" in leaf_id:
        return True
    return False


def leaf_sort_key(leaf_id: str) -> tuple[int, str]:
    if is_nordic(leaf_id):
        return (0, leaf_id)
    if is_europe(leaf_id):
        return (1, leaf_id)
    return (2, leaf_id)


def order_leaves(leaves: Iterable[str]) -> list[str]:
    return sorted(leaves, key=leaf_sort_key)


def load_processed(path: Path) -> dict:
    if not path.is_file():
        return {"release_version": None, "leaves": {}}
    return json.loads(path.read_text())


def save_processed_atomic(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    tmp.replace(path)


def pending_leaves(
    all_leaves: Iterable[str],
    processed: dict,
    *,
    leaf_filter: str | None = None,
    limit: int = 0,
) -> list[str]:
    done = set((processed.get("leaves") or {}).keys())
    ordered = order_leaves(all_leaves)
    out = []
    for leaf in ordered:
        if leaf_filter and leaf != leaf_filter:
            continue
        if leaf in done:
            continue
        out.append(leaf)
        if limit and len(out) >= limit:
            break
    return out
