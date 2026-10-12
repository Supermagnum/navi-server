#!/usr/bin/env bash
# Migrate navi-server runtime data to another host over SSH (rsync).
#
# Transfers (source tree → destination):
#   packs    data/published/     (HTTP DocumentRoot: packs, datex, repeaters, …)
#   jobs     data/state/, config.env, regions*.conf, CURRENT_GENERATION,
#            optional plugin work dirs (datex_npra, radioid, repeaterbook),
#            plus a systemd enablement inventory for remote re-enable
#   secrets  data/secrets/       (rsync -a preserves mode 0600; never logged)
#
# Skipped by default (large / dangerous):
#   scratch/, staging/, generations/, elevation*/
#   data/live and data/previous absolute symlinks (see docs/vps-prelaunch-checklist.md)
#
# Usage:
#   ./migrate-server.sh --dest user@host
#   ./migrate-server.sh --dest user@host:/media/navi/navi-server --dry-run
#   ./migrate-server.sh --dest user@host --verify-only
#   ./migrate-server.sh --dest user@host --components packs,secrets
#   ./migrate-server.sh --dest user@host --with-elevation
#   ./migrate-server.sh --local-dest /path/to/navi-server   # no SSH (tests / same host)
#
# Env overrides (tests / wrappers):
#   MIGRATE_SSH_CMD   default: ssh
#   MIGRATE_RSYNC     default: rsync
#
# Never prints secret file contents.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

SSH_CMD="${MIGRATE_SSH_CMD:-ssh}"
RSYNC_BIN="${MIGRATE_RSYNC:-rsync}"
DEFAULT_REMOTE_ROOT="/media/navi/navi-server"
SERVICE_USER="${NAVI_SERVICE_USER:-navit-server}"

DEST_SPEC=""
LOCAL_DEST=""
DRY_RUN=0
VERIFY_ONLY=0
WITH_ELEVATION=0
COMPONENTS="packs,jobs,secrets"
SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new)

usage() {
  sed -n '2,30p' "$0"
  exit "${1:-0}"
}

log() { echo "[migrate] $*"; }

parse_dest() {
  local spec="$1"
  if [[ "$spec" != *@* ]]; then
    die "--dest must be user@host or user@host:/path (got: $spec)"
  fi
  if [[ "$spec" == *:*/* || "$spec" == *:/* ]]; then
    DEST_HOST="${spec%%:*}"
    DEST_ROOT="${spec#*:}"
  else
    DEST_HOST="$spec"
    DEST_ROOT="$DEFAULT_REMOTE_ROOT"
  fi
  [[ -n "$DEST_HOST" && -n "$DEST_ROOT" ]] || die "could not parse --dest=$spec"
}

want_component() {
  local c="$1"
  [[ ",${COMPONENTS}," == *",${c},"* ]]
}

is_local() {
  [[ -n "$LOCAL_DEST" ]]
}

remote_sh() {
  # Run a bash snippet on the destination (stdin = script).
  if is_local; then
    DEST_ROOT="$DEST_ROOT" SERVICE_USER="$SERVICE_USER" COMPONENTS="$COMPONENTS" \
      bash -s
  else
    "$SSH_CMD" "${SSH_OPTS[@]}" "$DEST_HOST" \
      env DEST_ROOT="$DEST_ROOT" SERVICE_USER="$SERVICE_USER" COMPONENTS="$COMPONENTS" \
      bash -s
  fi
}

remote_cmd() {
  # Run a single remote shell command string.
  if is_local; then
    bash -c "$1"
  else
    "$SSH_CMD" "${SSH_OPTS[@]}" "$DEST_HOST" bash -c "$(printf '%q' "$1")"
  fi
}

rsync_path() {
  # Copy src (file or dir) to DEST_ROOT/relpath (dir trailing slash semantics for dirs).
  local src="$1" rel="$2"
  local dest_path="${DEST_ROOT}/${rel}"
  local -a args=(-a)
  [[ "$DRY_RUN" -eq 1 ]] && args+=(--dry-run)
  if is_local; then
    if [[ "$DRY_RUN" -eq 1 ]]; then
      log "dry-run local: ${src} -> ${dest_path}"
      return 0
    fi
    mkdir -p "$(dirname "$dest_path")"
    if [[ -d "$src" ]]; then
      mkdir -p "$dest_path"
      "$RSYNC_BIN" "${args[@]}" "${src%/}/" "${dest_path}/"
    else
      mkdir -p "$(dirname "$dest_path")"
      "$RSYNC_BIN" "${args[@]}" "$src" "$dest_path"
    fi
  else
    log "rsync ${src} -> ${DEST_HOST}:${dest_path}"
    if [[ -d "$src" ]]; then
      remote_cmd "mkdir -p $(printf '%q' "$dest_path")"
      "$RSYNC_BIN" "${args[@]}" -e "$SSH_CMD ${SSH_OPTS[*]}" \
        "${src%/}/" "${DEST_HOST}:${dest_path}/"
    else
      remote_cmd "mkdir -p $(printf '%q' "$(dirname "$dest_path")")"
      "$RSYNC_BIN" "${args[@]}" -e "$SSH_CMD ${SSH_OPTS[*]}" \
        "$src" "${DEST_HOST}:${dest_path}"
    fi
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dest) DEST_SPEC="${2:-}"; shift 2 ;;
    --local-dest) LOCAL_DEST="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --verify-only) VERIFY_ONLY=1; shift ;;
    --with-elevation) WITH_ELEVATION=1; shift ;;
    --components) COMPONENTS="${2:-}"; shift 2 ;;
    -h|--help) usage 0 ;;
    *) die "unknown arg: $1 (see --help)" ;;
  esac
done

COMPONENTS="${COMPONENTS// /}"

if [[ -n "$LOCAL_DEST" ]]; then
  DEST_HOST="local"
  DEST_ROOT="$(cd "$LOCAL_DEST" 2>/dev/null && pwd || true)"
  [[ -n "$DEST_ROOT" ]] || { mkdir -p "$LOCAL_DEST"; DEST_ROOT="$(cd "$LOCAL_DEST" && pwd)"; }
elif [[ -n "$DEST_SPEC" ]]; then
  parse_dest "$DEST_SPEC"
  require_cmd "$SSH_CMD"
else
  die "required: --dest user@host[/path] or --local-dest /path"
fi

require_cmd "$RSYNC_BIN" python3

SRC_ROOT="${NAVI_SERVER_ROOT}"
SRC_DATA="${SRC_ROOT}/data"
if [[ -f "${SRC_DATA}/config.env" ]]; then
  # shellcheck disable=SC1090
  set -a
  # shellcheck disable=SC1091
  source "${SRC_DATA}/config.env"
  set +a
  SERVICE_USER="${NAVI_SERVICE_USER:-$SERVICE_USER}"
  if [[ -n "${NAVI_PACK_ROOT:-}" && -d "${NAVI_PACK_ROOT}" ]]; then
    SRC_DATA="$NAVI_PACK_ROOT"
  fi
fi

log "source_data=${SRC_DATA}"
log "dest=${DEST_HOST}:${DEST_ROOT}"
log "components=${COMPONENTS} dry_run=${DRY_RUN} verify_only=${VERIFY_ONLY} local=$(is_local && echo 1 || echo 0)"

# --- preflight ---
if is_local; then
  [[ -d "${DEST_ROOT}/scripts" ]] || die "local dest missing scripts/: ${DEST_ROOT}"
else
  log "preflight: SSH BatchMode to ${DEST_HOST}"
  remote_cmd "test -d $(printf '%q' "$DEST_ROOT")" \
    || die "remote root missing: ${DEST_ROOT}"
  remote_cmd "test -d $(printf '%q' "${DEST_ROOT}/scripts")" \
    || die "remote missing scripts/ (install navi-server checkout first)"
  remote_cmd "command -v rsync >/dev/null && command -v python3 >/dev/null" \
    || die "remote needs rsync + python3"
fi

INVENTORY_DIR="$(mktemp -d "${TMPDIR:-/tmp}/navi-migrate.XXXXXX")"
trap 'rm -rf "$INVENTORY_DIR"' EXIT

write_jobs_inventory() {
  local out="${INVENTORY_DIR}/jobs-inventory.json"
  python3 - "$out" "$SERVICE_USER" <<'PY'
import json, subprocess, sys
out, user = sys.argv[1], sys.argv[2]
units = [
    "navi-pack-bake.timer",
    "navi-pack-scrub.timer",
    "navi-ddns.timer",
    "navi-datex-npra.timer",
    "navi-repeaterbook.timer",
    "navi-planet-leaves-watchdog.timer",
]
state = {"service_user": user, "timers": {}}
for u in units:
    try:
        en = subprocess.check_output(
            ["systemctl", "is-enabled", u], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        en = "absent"
    try:
        ac = subprocess.check_output(
            ["systemctl", "is-active", u], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        ac = "unknown"
    state["timers"][u] = {"enabled": en, "active": ac}
with open(out, "w", encoding="utf-8") as f:
    json.dump(state, f, indent=2, sort_keys=True)
    f.write("\n")
print(out)
PY
}

verify_dest() {
  log "verify: destination working-order checks"
  remote_sh <<'REMOTE'
set -euo pipefail
ROOT="$DEST_ROOT"
DATA="${ROOT}/data"
fail=0
ok() { echo "OK $*"; }
bad() { echo "FAIL $*"; fail=1; }

[[ -d "$ROOT/scripts" ]] && ok "checkout scripts/" || bad "checkout scripts/"
[[ -d "$DATA" ]] && ok "data/" || bad "data/"

if [[ ",${COMPONENTS}," == *",packs,"* ]]; then
  [[ -d "$DATA/published/packs" ]] && ok "published/packs" || bad "published/packs"
  if [[ -f "$DATA/published/current.json" ]]; then
    python3 -c 'import json,sys; json.load(open(sys.argv[1])); print("OK current.json parseable")' \
      "$DATA/published/current.json" || bad "current.json parse"
  else
    echo "WARN published/current.json absent"
  fi
  if [[ -x "$ROOT/scripts/verify-published-checksums.sh" ]]; then
    sample=$(find "$DATA/published/packs" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | head -1 || true)
    if [[ -n "${sample:-}" ]]; then
      rel=${sample#"$DATA/published/packs/"}
      region=${rel%/*}
      if NAVI_PACK_ROOT="$DATA" "$ROOT/scripts/verify-published-checksums.sh" \
          --region "$region" >/tmp/navi-migrate-checksum.out 2>&1; then
        ok "checksum sample region=${region}"
      else
        # Incomplete fixture gens (no checksums.sha256) are WARN not FAIL.
        if grep -q 'checksums.sha256' /tmp/navi-migrate-checksum.out 2>/dev/null; then
          echo "WARN checksum sample skipped/incomplete for ${region}"
        else
          bad "checksum sample region=${region}"
          tail -n 15 /tmp/navi-migrate-checksum.out || true
        fi
      fi
    else
      echo "WARN no pack generations to checksum"
    fi
  fi
fi

if [[ ",${COMPONENTS}," == *",secrets,"* ]]; then
  if [[ -d "$DATA/secrets" ]]; then
    mode=$(stat -c '%a' "$DATA/secrets" 2>/dev/null || echo '?')
    [[ "$mode" == "700" || "$mode" == "750" ]] && ok "secrets/ mode=${mode}" \
      || echo "WARN secrets/ mode=${mode} (prefer 700)"
    while IFS= read -r -d '' f; do
      m=$(stat -c '%a' "$f")
      own=$(stat -c '%U' "$f")
      base=$(basename "$f")
      if [[ "$m" != "600" && "$m" != "400" ]]; then
        bad "secret ${base} mode=${m} (want 0600)"
      else
        ok "secret ${base} mode=${m} owner=${own} (contents not shown)"
      fi
    done < <(find "$DATA/secrets" -type f -print0 2>/dev/null)
  else
    echo "WARN secrets/ absent"
  fi
fi

if [[ ",${COMPONENTS}," == *",jobs,"* ]]; then
  [[ -f "$DATA/config.env" ]] && ok "config.env" || bad "config.env"
  if [[ -f "$DATA/config.env" ]]; then
    if bash -c 'set -a; source "$1"; set +a' _ "$DATA/config.env" 2>/tmp/navi-migrate-source.err; then
      ok "config.env sources"
    else
      bad "config.env source failed"
      cat /tmp/navi-migrate-source.err || true
    fi
  fi
  [[ -d "$DATA/state" ]] && ok "state/" || echo "WARN state/ absent"
  if [[ -f "$DATA/state/migrate-jobs-inventory.json" ]]; then
    ok "jobs inventory present"
    python3 -c 'import json,sys; json.load(open(sys.argv[1])); print("OK jobs inventory parseable")' \
      "$DATA/state/migrate-jobs-inventory.json" || bad "jobs inventory parse"
  else
    echo "WARN jobs inventory missing"
  fi
  for link in live previous; do
    p="$DATA/$link"
    if [[ -L "$p" ]]; then
      tgt=$(readlink "$p" || true)
      if [[ "$tgt" == /* && ! -e "$p" ]]; then
        bad "dangling symlink data/${link} -> ${tgt}"
      else
        ok "data/${link} symlink ok"
      fi
    fi
  done
  if id -u "$SERVICE_USER" >/dev/null 2>&1; then
    ok "service user ${SERVICE_USER}"
  else
    echo "WARN service user ${SERVICE_USER} missing — run setup-server.sh --apply-service on dest"
  fi
  if [[ -x "$ROOT/scripts/setup-server.sh" ]]; then
    if "$ROOT/scripts/setup-server.sh" --check >/tmp/navi-migrate-check.out 2>&1; then
      ok "setup-server.sh --check"
    else
      echo "WARN setup-server.sh --check issues:"
      grep -E '^(OK|MISSING|FAIL|WARN)' /tmp/navi-migrate-check.out | head -40 || true
    fi
  fi
fi

exit "$fail"
REMOTE
}

if [[ "$VERIFY_ONLY" -eq 1 ]]; then
  verify_dest
  log "verify-only complete"
  exit 0
fi

# --- transfer ---
if want_component packs; then
  [[ -d "${SRC_DATA}/published" ]] || die "missing ${SRC_DATA}/published"
  remote_cmd "mkdir -p $(printf '%q' "${DEST_ROOT}/data/published")"
  rsync_path "${SRC_DATA}/published" "data/published"
fi

if want_component secrets; then
  if [[ -d "${SRC_DATA}/secrets" ]]; then
    remote_cmd "mkdir -p $(printf '%q' "${DEST_ROOT}/data/secrets") && chmod 700 $(printf '%q' "${DEST_ROOT}/data/secrets")"
    rsync_path "${SRC_DATA}/secrets" "data/secrets"
    if [[ "$DRY_RUN" -eq 0 ]]; then
      remote_cmd "chmod 700 $(printf '%q' "${DEST_ROOT}/data/secrets") && find $(printf '%q' "${DEST_ROOT}/data/secrets") -type f -exec chmod 600 {} +"
    fi
  else
    log "WARN: no local secrets/ — skipping"
  fi
fi

if want_component jobs; then
  write_jobs_inventory >/dev/null
  mkdir -p "${SRC_DATA}/state"
  if [[ "$DRY_RUN" -eq 0 ]]; then
    cp "${INVENTORY_DIR}/jobs-inventory.json" "${SRC_DATA}/state/migrate-jobs-inventory.json"
  fi
  remote_cmd "mkdir -p $(printf '%q' "${DEST_ROOT}/data/state")"

  for item in state config.env CURRENT_GENERATION regions.conf regions.planet.conf \
    regions.conf.bboxes.json regions.planet.conf.bboxes.json \
    datex_npra radioid repeaterbook ddns.env; do
    src="${SRC_DATA}/${item}"
    [[ -e "$src" ]] || continue
    rsync_path "$src" "data/${item}"
  done

  if [[ "$DRY_RUN" -eq 1 ]]; then
    log "dry-run: would install state/migrate-jobs-inventory.json"
  elif [[ ! -f "${SRC_DATA}/state/migrate-jobs-inventory.json" ]]; then
    rsync_path "${INVENTORY_DIR}/jobs-inventory.json" "data/state/migrate-jobs-inventory.json"
  fi

  if [[ "$DRY_RUN" -eq 0 ]]; then
    # Rewrite common absolute path prefixes in config.env toward dest root.
    DEST_ROOT="$DEST_ROOT" remote_sh <<'REMOTE'
set -euo pipefail
cfg="${DEST_ROOT}/data/config.env"
[[ -f "$cfg" ]] || exit 0
python3 - "$cfg" "$DEST_ROOT" <<'PY'
import pathlib, sys
cfg = pathlib.Path(sys.argv[1])
dest = sys.argv[2].rstrip("/")
text = cfg.read_text(encoding="utf-8")
for old in ("/media/navi/navi-server", "/opt/navi-server", "/srv/navi-server"):
    if old != dest:
        text = text.replace(old, dest)
cfg.write_text(text, encoding="utf-8")
print("rewrote absolute path prefixes in config.env toward", dest)
PY
REMOTE
  fi
fi

if [[ "$WITH_ELEVATION" -eq 1 ]]; then
  for elev in elevation elevation-planet; do
    if [[ -d "${SRC_DATA}/${elev}" ]]; then
      remote_cmd "mkdir -p $(printf '%q' "${DEST_ROOT}/data/${elev}")"
      rsync_path "${SRC_DATA}/${elev}" "data/${elev}"
    fi
  done
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  log "dry-run complete — skipped verify"
  exit 0
fi

verify_dest

log "migration complete"
log "on dest: review data/state/migrate-jobs-inventory.json then enable timers:"
log "  sudo ${DEST_ROOT}/scripts/setup-server.sh --apply-service"
log "  (plus --apply-datex / --apply-ddns / --apply-repeaterbook / --apply-radioid as needed)"
if is_local; then
  log "re-check: $0 --local-dest ${DEST_ROOT} --verify-only"
else
  log "re-check: $0 --dest ${DEST_SPEC} --verify-only"
fi
