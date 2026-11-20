#!/usr/bin/env bash
# Apply open DATEX provider configs + shared fetcher to live navi-server.
# Does NOT touch NPRA secrets, no-vegvesen.toml, systemd DATEX enablement,
# or any MWM / pack-pipeline paths.
#
# Usage (exact):
#   sudo /media/navi/haaken-scratch/navi-server-mwm/scripts/apply-datex-open-feeds.sh
#
# Optional env:
#   SRC=… DST=… SKIP_POLL=1
set -euo pipefail

SRC="${SRC:-/media/navi/haaken-scratch/navi-server-mwm}"
DST="${DST:-/media/navi/navi-server}"
SERVICE_USER="${NAVI_SERVICE_USER:-navit-server}"
SKIP_POLL="${SKIP_POLL:-0}"

OPEN_PROVIDERS=(
  be-flanders
  fi-digitraffic
  fr-tipi
  lu-cita
  nl-ndw
  bg-ria
  pt-mobie
)

die() { echo "FATAL: $*" >&2; exit 1; }
log() { echo "[apply-datex-open-feeds] $*"; }

[[ "$(id -u)" -eq 0 ]] || die "run as root: sudo $0"
[[ -d "$SRC/config/datex/providers" ]] || die "missing SRC providers: $SRC"
[[ -d "$DST/config/datex/providers" ]] || die "missing DST providers: $DST"
[[ -f "$DST/config/datex/providers/no-vegvesen.toml" ]] \
  || die "live NPRA config missing — aborting (will not break NPRA layout)"
id -u "$SERVICE_USER" >/dev/null 2>&1 || die "service user $SERVICE_USER missing"

# Refuse to overwrite a divergent live NPRA toml (open-feeds must not touch it).
if ! cmp -s \
  "$SRC/config/datex/providers/no-vegvesen.toml" \
  "$DST/config/datex/providers/no-vegvesen.toml"; then
  log "NOTE: SRC and DST no-vegvesen.toml differ — leaving live NPRA config untouched"
fi

install_as_service() {
  local src="$1" dest="$2" mode="${3:-0644}"
  install -D -m "$mode" -o "$SERVICE_USER" -g "$SERVICE_USER" "$src" "$dest"
}

log "Installing shared DATEX fetcher / provider_spec (NPRA uses same module)…"
install_as_service \
  "$SRC/plugins/datex_common/fetcher.py" \
  "$DST/plugins/datex_common/fetcher.py"
install_as_service \
  "$SRC/plugins/datex_common/provider_spec.py" \
  "$DST/plugins/datex_common/provider_spec.py"
if [[ -f "$SRC/plugins/datex_common/tests/test_datex_providers.py" ]]; then
  install_as_service \
    "$SRC/plugins/datex_common/tests/test_datex_providers.py" \
    "$DST/plugins/datex_common/tests/test_datex_providers.py"
fi

log "Installing open provider tomls (not no-vegvesen)…"
for id in "${OPEN_PROVIDERS[@]}"; do
  src_toml="$SRC/config/datex/providers/${id}.toml"
  [[ -f "$src_toml" ]] || die "missing $src_toml"
  install_as_service "$src_toml" "$DST/config/datex/providers/${id}.toml"
done

if [[ -f "$SRC/docs/datex-open-feeds.md" ]]; then
  install_as_service "$SRC/docs/datex-open-feeds.md" "$DST/docs/datex-open-feeds.md"
fi
if [[ -f "$SRC/docs/datex-providers.md" ]]; then
  install_as_service "$SRC/docs/datex-providers.md" "$DST/docs/datex-providers.md"
fi

# Drop stale bytecode so the next poll loads new modules.
find "$DST/plugins/datex_common" -name '*.pyc' -delete 2>/dev/null || true
find "$DST/plugins/datex_common" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

log "Verifying live NPRA toml still present and unchanged by this script…"
[[ -f "$DST/config/datex/providers/no-vegvesen.toml" ]] || die "NPRA toml vanished"
# Secrets must remain untouched
if [[ -e /etc/navi-server/datex/no-vegvesen.env ]]; then
  log "NPRA secrets file present (not modified)"
fi

if [[ "$SKIP_POLL" == "1" ]]; then
  log "SKIP_POLL=1 — not polling. Timer will pick up new providers on next tick."
  log "Done."
  exit 0
fi

log "Force-polling open providers (and refreshing providers.json)…"
# Poll open feeds only — do not force-repoll NPRA here (avoids credential noise / load).
export PYTHONPATH="$DST"
export NAVI_PACK_ROOT="${DST}/data"
export NAVI_DATEX_PROVIDERS_DIR="${DST}/config/datex/providers"
rc=0
for id in "${OPEN_PROVIDERS[@]}"; do
  log "  test/poll $id"
  if ! runuser -u "$SERVICE_USER" -- env \
    PYTHONPATH="$DST" \
    NAVI_PACK_ROOT="${DST}/data" \
    NAVI_DATEX_PROVIDERS_DIR="${DST}/config/datex/providers" \
    "$DST/scripts/datex-provider.sh" test "$id"; then
    log "WARN: $id test returned non-zero (continuing)"
    rc=1
  fi
done

# Rebuild registry so /datex/providers.json lists new publish ids.
runuser -u "$SERVICE_USER" -- env \
  PYTHONPATH="$DST" \
  NAVI_PACK_ROOT="${DST}/data" \
  NAVI_DATEX_PROVIDERS_DIR="${DST}/config/datex/providers" \
  python3 - <<'PY'
from pathlib import Path
import os
from plugins.datex_common.provider_spec import load_all_providers
from plugins.datex_common.providers_index import rebuild_providers_index_from_specs

root = Path(os.environ["NAVI_PACK_ROOT"])
specs = load_all_providers(Path(os.environ["NAVI_DATEX_PROVIDERS_DIR"]))
out = rebuild_providers_index_from_specs(root, specs)
print("wrote", out)
PY

log "NPRA published tree check…"
[[ -d "${DST}/data/published/datex/npra" ]] \
  || die "missing published/datex/npra after apply"
[[ -f "${DST}/data/published/datex/npra/source.json" ]] \
  || log "WARN: npra/source.json missing (may be disabled / never polled)"

if systemctl is-enabled navi-datex-npra.timer >/dev/null 2>&1; then
  log "navi-datex-npra.timer still enabled (left running; polls all enabled providers)"
else
  log "WARN: navi-datex-npra.timer not enabled"
fi

[[ "$rc" -eq 0 ]] || die "one or more open provider tests failed"
log "Done. Open feeds installed; NPRA config/secrets untouched."
