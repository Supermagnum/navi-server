"""Frozen .rkyv publish tree: refuse bake/publish/prune while marker exists."""

from __future__ import annotations

import os
from pathlib import Path

FROZEN_MARKER_NAME = ".navi-rkyv-frozen"


class RkyvFrozenError(RuntimeError):
    """Raised when a .rkyv mutate entry point runs against a frozen tree."""


def frozen_marker_path(published_dir: Path | str) -> Path:
    return Path(published_dir) / FROZEN_MARKER_NAME


def is_rkyv_frozen(published_dir: Path | str) -> bool:
    return frozen_marker_path(published_dir).is_file()


def allow_frozen_override() -> bool:
    raw = os.environ.get("NAVI_RKYV_ALLOW_FROZEN", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def refuse_if_frozen(
    published_dir: Path | str,
    *,
    entry: str,
    allow_override: bool | None = None,
) -> None:
    """Abort if ``.navi-rkyv-frozen`` exists unless override is set.

    Override: ``NAVI_RKYV_ALLOW_FROZEN=1`` or ``allow_override=True``.
    """
    if allow_override is None:
        allow_override = allow_frozen_override()
    marker = frozen_marker_path(published_dir)
    if not marker.is_file():
        return
    if allow_override:
        return
    raise RkyvFrozenError(
        f"{entry}: refusing to run — {marker} exists "
        f"(serving-only freeze). Override with NAVI_RKYV_ALLOW_FROZEN=1 "
        f"or --allow-frozen only for deliberate maintenance."
    )


def rkyv_bake_timer_enabled(
    *,
    unit: str = "navi-pack-bake.timer",
    published_dir: Path | str | None = None,
) -> bool:
    """Whether the weekly .rkyv bake is considered active for MWM schedule guard.

    Precedence:
    1. ``NAVI_MWM_RKYV_BAKE_TIMER_ENABLED`` = 0/1 (explicit)
    2. ``.navi-rkyv-frozen`` present → False
    3. ``systemctl is-enabled <unit>`` → enabled/disabled
    4. default True (conservative: keep pausing if unknown)
    """
    raw = os.environ.get("NAVI_MWM_RKYV_BAKE_TIMER_ENABLED", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    if published_dir is not None and is_rkyv_frozen(published_dir):
        return False
    pub = os.environ.get("NAVI_PUBLISHED_DIR", "").strip()
    if pub and is_rkyv_frozen(pub):
        return False
    try:
        import subprocess

        r = subprocess.run(
            ["systemctl", "is-enabled", unit],
            capture_output=True,
            text=True,
            timeout=5,
        )
        state = (r.stdout or "").strip()
        if state == "enabled":
            return True
        if state in ("disabled", "masked", "static"):
            # static is unusual for a timer; treat non-enabled as inactive
            return False
        if r.returncode != 0:
            return False
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return True
