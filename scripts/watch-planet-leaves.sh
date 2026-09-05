#!/usr/bin/env bash
# Alert-only watchdog for the planet-leaves orchestrator.
# Does NOT resume or start the bake — only appends to
# ${NAVI_LOG_DIR}/orchestrator-watchdog.log when the expected process is gone
# without a clear "still holding after PAUSED" state.
#
# Intended for cron or systemd/navi-planet-leaves-watchdog.timer.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
load_config

export SCREENDIR="${SCREENDIR:-$HOME/.screen}"
WATCH_LOG="${NAVI_LOG_DIR}/orchestrator-watchdog.log"
PAUSE_FILE="${NAVI_LOG_DIR}/planet-leaves/PAUSED"
SESSION_NAME="navi-planet-leaves"
mkdir -p "$(dirname "$WATCH_LOG")" "$NAVI_LOG_DIR"

ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
screen_up=0
proc_up=0

if screen -ls 2>/dev/null | grep -q "\.${SESSION_NAME}[[:space:]]"; then
  screen_up=1
fi
if pgrep -f 'run-planet-leaves-batched\.sh' >/dev/null 2>&1; then
  proc_up=1
fi

paused=0
pause_reason=""
if [[ -f "$PAUSE_FILE" ]]; then
  paused=1
  pause_reason="$(tr '\n' ' ' <"$PAUSE_FILE" | head -c 200)"
fi

status_line="screen_up=${screen_up} proc_up=${proc_up} paused=${paused}"

# Healthy cases: orchestrator running (baking or PAUSED-hold).
if [[ "$proc_up" -eq 1 ]]; then
  # Optional heartbeat at debug level — keep quiet on success to avoid noise.
  exit 0
fi

# No orchestrator process.
if [[ "$paused" -eq 1 ]]; then
  msg="${ts} ALERT orchestrator missing while PAUSED file present (${status_line}) reason=${pause_reason}"
  printf '%s\n' "$msg" | tee -a "$WATCH_LOG"
  exit 1
fi

# No process and no PAUSED: either idle between runs (OK if never started) or
# silent disappearance. Alert when a prior run.log exists (bake has been used).
if [[ -f "${NAVI_LOG_DIR}/planet-leaves/run.log" ]]; then
  # Avoid alerting before the first ever start: require a START/RESUME in log.
  if rg -q 'run-planet-leaves-batched|RESUME utc=|START utc=' \
      "${NAVI_LOG_DIR}/planet-leaves/run.log" 2>/dev/null; then
    msg="${ts} ALERT orchestrator not running and no PAUSED marker (${status_line}) — silent disappearance or clean exit without hold"
    printf '%s\n' "$msg" | tee -a "$WATCH_LOG"
    exit 1
  fi
fi

exit 0
