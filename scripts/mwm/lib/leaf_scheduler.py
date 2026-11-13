#!/usr/bin/env python3
"""Parallel leaf scheduling for CoMaps-release bake.

Downloads stay at polite concurrency (default 2) within a bounded prefetch disk
budget (default 20 GiB). Processing concurrency derives from free RAM and
per-stage peak RSS from measured-peaks.json (with a separate semaphore for the
wetland annotate stage, default 2). Override with NAVI_MWM_PROCESS_CONCURRENCY.

Order: Nordic first, then remaining Europe, then World / rest.
Resume via processed.json (atomic write under separate lock). Safe under
systemd Restart=on-failure when the outer bake holds pack-pipeline.lock for the
whole run. Never start while the weekly .rkyv bake holds pack-pipeline.lock.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

# Legacy worst-case single-leaf peak RSS (gates2 Nordic /usr/bin/time -v).
DEFAULT_PER_LEAF_GIB = 8.2
DEFAULT_HEADROOM_GIB = 16.0
DEFAULT_ANNOTATE_CONCURRENCY = 2
DEFAULT_PREFETCH_DISK_BUDGET_GIB = 20.0

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


def stage_aware_process_concurrency(
    *,
    stage_peaks_gib: dict[str, float],
    annotate_concurrency: int = DEFAULT_ANNOTATE_CONCURRENCY,
    headroom_gib: float = DEFAULT_HEADROOM_GIB,
    available_gib: float | None = None,
    max_concurrency: int | None = None,
) -> int:
    """Derive leaf concurrency from per-stage peak RSS and annotate semaphore.

    At most `annotate_concurrency` workers may occupy the wetland annotate stage
    simultaneously; remaining workers run dump/elevation/bake bounded by the
    maximum of those non-annotate stage peaks.
    """
    avail = available_ram_gib() if available_gib is None else available_gib
    usable = avail - headroom_gib
    if usable <= 0:
        return 1

    ann_peak = max(0.1, float(stage_peaks_gib.get("annotate", DEFAULT_PER_LEAF_GIB)))
    other_peak = max(
        0.1,
        float(stage_peaks_gib.get("dump", 0.5)),
        float(stage_peaks_gib.get("elevation", 1.8)),
        float(stage_peaks_gib.get("bake", 0.3)),
    )
    ann_cap = max(1, int(annotate_concurrency))
    cap = max_concurrency if (max_concurrency and max_concurrency > 0) else 64

    best = 1
    for n in range(1, cap + 1):
        ann_slots = min(n, ann_cap)
        other_slots = n - ann_slots
        needed = ann_slots * ann_peak + other_slots * other_peak
        if needed <= usable:
            best = n
        else:
            break
    return max(1, best)


def _measured_peaks_path() -> Path:
    path = os.environ.get("NAVI_MWM_MEASURED_PEAKS", "").strip()
    if path:
        return Path(path)
    root = Path(__file__).resolve().parents[3]
    return root / "config" / "mwm" / "measured-peaks.json"


def _load_measured_peaks_json(path: Path | None = None) -> dict:
    p = path or _measured_peaks_path()
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def _load_measured_per_leaf_gib(path: Path | None = None) -> float:
    data = _load_measured_peaks_json(path)
    v = data.get("comaps_release_process_peak_rss_gib")
    if v is not None:
        try:
            return float(v)
        except (ValueError, TypeError):
            pass
    return DEFAULT_PER_LEAF_GIB


def load_stage_peaks_gib(
    measured_peaks_path: Path | None = None,
    stats_dir: Path | None = None,
) -> dict[str, float] | None:
    """Return per-stage peak RSS in GiB from measured-peaks.json and optional stats."""
    data = _load_measured_peaks_json(measured_peaks_path)
    raw_stages = data.get("stage_peaks_gib")
    peaks: dict[str, float] = {}
    if isinstance(raw_stages, dict):
        for k in ("dump", "annotate", "elevation", "bake"):
            if k in raw_stages and raw_stages[k] is not None:
                try:
                    peaks[k] = float(raw_stages[k])
                except (ValueError, TypeError):
                    pass

    if stats_dir is not None and stats_dir.is_dir():
        samples: dict[str, list[float]] = {k: [] for k in ("dump", "annotate", "elevation", "bake")}
        for p in stats_dir.glob("*.timings.json"):
            try:
                t = json.loads(p.read_text())
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
            rss_map = t.get("peak_rss_kib")
            if not isinstance(rss_map, dict):
                continue
            for k in samples:
                v = rss_map.get(k)
                if v is None:
                    continue
                try:
                    samples[k].append(float(v) / (1024.0 * 1024.0))
                except (ValueError, TypeError):
                    continue
        if any(len(vals) >= 20 for vals in samples.values()):
            for k, vals in samples.items():
                if not vals:
                    continue
                vals.sort()
                p95_idx = min(len(vals) - 1, int(0.95 * (len(vals) - 1)))
                p95_val = vals[p95_idx]
                peaks[k] = max(peaks.get(k, 0.0), p95_val) if peaks else p95_val

    return peaks if peaks else None


def resolve_annotate_concurrency(measured_peaks_path: Path | None = None) -> int:
    raw = os.environ.get("NAVI_MWM_ANNOTATE_CONCURRENCY", "").strip()
    if raw and raw != "0":
        return max(1, int(raw))
    data = _load_measured_peaks_json(measured_peaks_path)
    v = data.get("annotate_concurrency")
    if v is not None:
        try:
            return max(1, int(v))
        except (ValueError, TypeError):
            pass
    return DEFAULT_ANNOTATE_CONCURRENCY


def resolve_prefetch_disk_budget_bytes(measured_peaks_path: Path | None = None) -> int:
    raw = os.environ.get("NAVI_MWM_PREFETCH_DISK_BUDGET_GIB", "").strip()
    if raw:
        gib = float(raw)
    else:
        data = _load_measured_peaks_json(measured_peaks_path)
        gib = float(data.get("prefetch_disk_budget_gib", DEFAULT_PREFETCH_DISK_BUDGET_GIB))
    return max(0, int(gib * (1024**3)))


def resolve_process_concurrency(
    *,
    stats_dir: Path | None = None,
    measured_peaks_path: Path | None = None,
    available_gib: float | None = None,
) -> int:
    """Explicit override wins; 0 / empty / unset → derive from RAM + stage peaks."""
    raw = os.environ.get("NAVI_MWM_PROCESS_CONCURRENCY", "").strip()
    if raw and raw != "0":
        return max(1, int(raw))

    head = float(os.environ.get("NAVI_MWM_RAM_HEADROOM_GIB", str(DEFAULT_HEADROOM_GIB)) or DEFAULT_HEADROOM_GIB)
    per_env = os.environ.get("NAVI_MWM_PER_LEAF_GIB", "").strip()
    if per_env:
        return default_process_concurrency(
            per_leaf_gib=float(per_env),
            headroom_gib=head,
            available_gib=available_gib,
        )

    stage_peaks = load_stage_peaks_gib(measured_peaks_path=measured_peaks_path, stats_dir=stats_dir)
    if stage_peaks is not None:
        ann_n = resolve_annotate_concurrency(measured_peaks_path=measured_peaks_path)
        max_raw = os.environ.get("NAVI_MWM_MAX_PROCESS_CONCURRENCY", "").strip()
        max_n = int(max_raw) if max_raw and int(max_raw) > 0 else None
        return stage_aware_process_concurrency(
            stage_peaks_gib=stage_peaks,
            annotate_concurrency=ann_n,
            headroom_gib=head,
            available_gib=available_gib,
            max_concurrency=max_n,
        )

    per = _load_measured_per_leaf_gib(measured_peaks_path)
    return default_process_concurrency(
        per_leaf_gib=per,
        headroom_gib=head,
        available_gib=available_gib,
    )


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
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp = Path(tmp_name)
    try:
        payload = (json.dumps(state, indent=2) + "\n").encode("utf-8")
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


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


_WEEKDAY_ALIASES = {
    "mon": 0,
    "monday": 0,
    "tue": 1,
    "tuesday": 1,
    "wed": 2,
    "wednesday": 2,
    "thu": 3,
    "thursday": 3,
    "fri": 4,
    "friday": 4,
    "sat": 5,
    "saturday": 5,
    "sun": 6,
    "sunday": 6,
}


def _parse_weekday(raw: str) -> int:
    s = raw.strip().lower()
    if s in _WEEKDAY_ALIASES:
        return _WEEKDAY_ALIASES[s]
    val = int(s)
    if not 0 <= val <= 6:
        raise ValueError(f"weekday must be 0..6, got {raw!r}")
    return val


def _parse_hhmm(raw: str) -> tuple[int, int]:
    s = raw.strip()
    if ":" in s:
        hh_s, mm_s = s.split(":", 1)
        hh, mm = int(hh_s), int(mm_s)
    else:
        hh, mm = int(s), 0
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"invalid UTC HH:MM {raw!r}")
    return hh, mm


@dataclass(frozen=True)
class ScheduleGuardConfig:
    enabled: bool = True
    weekly_weekday_utc: int = 0  # Monday
    weekly_hour_utc: int = 0
    weekly_minute_utc: int = 0
    pause_before_hours: float = 6.0
    pause_after_hours: float = 6.0
    done_marker_path: Path | None = None
    lock_path: Path | None = None


def load_schedule_guard_config() -> ScheduleGuardConfig:
    enabled = os.environ.get("NAVI_MWM_SCHEDULE_GUARD", "1").strip() not in ("0", "false", "no", "off")
    weekday = _parse_weekday(os.environ.get("NAVI_MWM_WEEKLY_BAKE_WEEKDAY", "0") or "0")
    hour, minute = _parse_hhmm(os.environ.get("NAVI_MWM_WEEKLY_BAKE_UTC", "00:00") or "00:00")
    before_h = max(0.0, float(os.environ.get("NAVI_MWM_PAUSE_BEFORE_HOURS", "6") or "6"))
    after_h = max(0.0, float(os.environ.get("NAVI_MWM_PAUSE_AFTER_HOURS", "6") or "6"))
    done_raw = os.environ.get("NAVI_MWM_WEEKLY_BAKE_DONE_MARKER", "").strip()
    lock_raw = os.environ.get("NAVI_MWM_WEEKLY_BAKE_LOCK", "").strip()
    return ScheduleGuardConfig(
        enabled=enabled,
        weekly_weekday_utc=weekday,
        weekly_hour_utc=hour,
        weekly_minute_utc=minute,
        pause_before_hours=before_h,
        pause_after_hours=after_h,
        done_marker_path=Path(done_raw) if done_raw else None,
        lock_path=Path(lock_raw) if lock_raw else None,
    )


def _is_flock_held(lock_path: Path) -> bool:
    if not lock_path.exists():
        return False
    try:
        with lock_path.open("r+") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                return False
            except BlockingIOError:
                return True
    except OSError:
        return False


def evaluate_schedule_guard(
    now_utc: datetime | None = None,
    cfg: ScheduleGuardConfig | None = None,
) -> tuple[bool, str, datetime | None]:
    """Return (should_pause, reason, window_end_utc).

    Pauses from `pause_before_hours` before weekly UTC bake until either:
    - `done_marker_path` is touched at/after bake start, OR
    - `pause_after_hours` past bake start elapses (and external `lock_path` is not held).
    """
    cfg = cfg or load_schedule_guard_config()
    if not cfg.enabled:
        return False, "disabled", None

    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    if cfg.lock_path is not None and _is_flock_held(cfg.lock_path):
        return True, f"weekly_bake_lock_held:{cfg.lock_path}", None

    days_since = (now.weekday() - cfg.weekly_weekday_utc) % 7
    base_day = (now - timedelta(days=days_since)).replace(
        hour=cfg.weekly_hour_utc,
        minute=cfg.weekly_minute_utc,
        second=0,
        microsecond=0,
    )
    before_td = timedelta(hours=cfg.pause_before_hours)
    after_td = timedelta(hours=cfg.pause_after_hours)

    for week_shift in (-7, 0, 7):
        bake_start = base_day + timedelta(days=week_shift)
        win_start = bake_start - before_td
        win_end = bake_start + after_td
        if win_start <= now < win_end:
            if (
                now >= bake_start
                and cfg.done_marker_path is not None
                and cfg.done_marker_path.is_file()
            ):
                try:
                    mtime = cfg.done_marker_path.stat().st_mtime
                    if mtime >= bake_start.timestamp():
                        return False, "weekly_bake_done_marker", None
                except OSError:
                    pass
            return True, f"weekly_bake_window:{bake_start.strftime('%Y-%m-%dT%H:%MZ')}", win_end

    return False, "outside_window", None
