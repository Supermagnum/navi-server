#!/usr/bin/env bash
# Resume after a completed leaf bake: validate (incl. route check) → region
# mapping → publish-mwm. Does NOT re-run parallel leaf bake / elevation.
#
# Typical use after routes_builder_tool was missing at the end of a world bake:
#   sudo -u navit-server bash -c 'set -a; source /etc/navi/mwm.env; set +a;
#     /opt/navi/navi-server/scripts/mwm/finish-comaps-release-postbake.sh \
#       --work-dir /media/navi/navi-server/data/mwm/maps_build/comaps-release/261115'
#
# --skip-publish keeps artifacts in publish-staging only (no current.json flip).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

WORK=""
SKIP_PUBLISH=0
SKIP_VALIDATE=0
PUBLISH_ROOT="${NAVI_MWM_PUBLISHED_ROOT}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --work-dir) WORK="$2"; shift 2 ;;
    --publish-root) PUBLISH_ROOT="$2"; shift 2 ;;
    --skip-publish) SKIP_PUBLISH=1; shift ;;
    --skip-validate) SKIP_VALIDATE=1; shift ;;
    *) mwm_die "unknown arg: $1" ;;
  esac
done
[[ -n "$WORK" && -d "$WORK" ]] || mwm_die "need --work-dir (comaps-release/<ver> work tree)"

ROOT="$(mwm_repo_root)"
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-${ROOT}/target}"
PYTHON="${NAVI_MWM_PYTHON:-python3}"

# Share pack-pipeline.lock with bake/publish (same as run-comaps-release-bake).
mwm_with_lock bash -c "
set -euo pipefail
source '${SCRIPT_DIR}/lib/common.sh'
mwm_load_defaults
SCRIPT_DIR='${SCRIPT_DIR}'
WORK='${WORK}'
SKIP_PUBLISH='${SKIP_PUBLISH}'
SKIP_VALIDATE='${SKIP_VALIDATE}'
PUBLISH_ROOT='${PUBLISH_ROOT}'
PYTHON='${PYTHON}'
export CARGO_TARGET_DIR='${CARGO_TARGET_DIR}'
export NAVI_MWM_PYTHON=\"\$PYTHON\"

VER=\"\$(basename \"\$WORK\")\"
VERSION_DIR=\"\${WORK}/publish-staging/\${VER}\"
STATE=\"\${WORK}/meta/processed.json\"
ATTR_JSON=\"\${WORK}/attribution.json\"
WORLD_LEAFSET=\"\${WORK}/meta/world-leafset.txt\"

[[ -f \"\$STATE\" ]] || mwm_die \"missing \${STATE}\"
[[ -d \"\${WORK}/sidecars\" ]] || mwm_die \"missing \${WORK}/sidecars\"
[[ -f \"\$ATTR_JSON\" ]] || mwm_die \"missing \${ATTR_JSON}\"

# Ensure routes_builder is installed before validate (production requires it).
# On live, pre-install as root via /tmp/navi-mwm-install-routes-builder.sh —
# navit-server cannot read haaken-scratch.
\"\${SCRIPT_DIR}/install-routes-builder-tool.sh\" >/dev/null

# World leafset for validate/publish when ALL_LEAVES baked this work tree.
if [[ -f \"\$WORLD_LEAFSET\" ]]; then
  export NAVI_MWM_LEAFSET=\"\$WORLD_LEAFSET\"
fi

# Re-stage from bake sidecars/ by content (idempotent; does not touch leaf bake).
# Never gate on leaf-count alone — count-matched stale staging missed NR fills.
mwm_log \"re-staging \${VERSION_DIR} from \${WORK}/sidecars (content sync)\"
rm -rf \"\$VERSION_DIR\"
mkdir -p \"\$VERSION_DIR\"
cp -a \"\${WORK}/sidecars/\"*.navi-sidecar.v1.zst \"\$VERSION_DIR/\" 2>/dev/null || true
cp -a \"\$ATTR_JSON\" \"\${VERSION_DIR}/attribution.json\"
[[ -f \"\${WORK}/ATTRIBUTION.md\" ]] && cp -a \"\${WORK}/ATTRIBUTION.md\" \"\${VERSION_DIR}/ATTRIBUTION.md\"
[[ -f \"\${WORK}/meta/discovery.json\" ]] && cp -a \"\${WORK}/meta/discovery.json\" \"\${VERSION_DIR}/discovery.json\"
cp -a \"\$STATE\" \"\${VERSION_DIR}/processed.json\"

SC_N=\"\$(find \"\$VERSION_DIR\" -maxdepth 1 -name '*.navi-sidecar.v1.zst' | wc -l)\"
mwm_log \"staged sidecars=\${SC_N} version=\${VER}\"
[[ \"\$SC_N\" -gt 0 ]] || mwm_die \"no sidecars staged under \${VERSION_DIR}\"

if [[ \"\$SKIP_VALIDATE\" != \"1\" ]]; then
  \"\${SCRIPT_DIR}/validate-comaps-release.sh\" --version-dir \"\$VERSION_DIR\" --work-dir \"\$WORK\" \
    || mwm_die \"validate failed\"
fi

# Region↔leaf migration + country_groups (same as run-comaps-release-bake.sh).
# Under set -u: never expand unbound NAVI_MWM_REGION_MAP — missing inputs → WARN skip.
# Missing lib/region_mapping.py (live lag) → WARN skip, not hard fail.
BORDERS=\"\${NAVI_MWM_BORDERS_DIR:-}\"
EXTRACTS=\"\${NAVI_MWM_EXTRACTS_DIR:-\${NAVI_EXTRACTS_DIR:-}}\"
REGIONS_JSON=\"\${NAVI_MWM_REGIONS_JSON:-}\"
REGION_MAP=\"\${NAVI_MWM_REGION_MAP:-}\"
SKIP_RMAP=\"\${NAVI_MWM_SKIP_REGION_MAP:-\${NAVI_VL_SKIP_REGION_MAP:-0}}\"
REGION_MAP_MOD=\"\${SCRIPT_DIR}/lib/region_mapping.py\"
COUNTRIES_TXT=\"\${WORK}/meta/countries.txt\"
if [[ ! -f \"\$COUNTRIES_TXT\" ]]; then
  COUNTRIES_TXT=\"\$(find \"\$WORK\" -name countries.txt -type f 2>/dev/null | head -1 || true)\"
fi
# Prefer release-matched countries.txt: download into work/meta when missing.
if [[ -z \"\$COUNTRIES_TXT\" || ! -f \"\$COUNTRIES_TXT\" ]]; then
  if [[ -f \"\${WORK}/meta/discovery.json\" ]]; then
    mwm_log \"fetching countries.txt for \${VER} into work/meta\"
    \"\$PYTHON\" - \"\$SCRIPT_DIR\" \"\${WORK}/meta/discovery.json\" \"\${WORK}/meta/countries.txt\" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'lib'))
from comaps_release import http_get_bytes
disc = json.loads(Path(sys.argv[2]).read_text())
series = disc['map_series']
ver = disc['version']
mirrors = disc.get('mirrors') or []
last = None
raw = None
for mirror in mirrors:
    url = f\"{mirror.rstrip('/')}/maps/{series}/{ver}/countries.txt\"
    try:
        raw = http_get_bytes(url, timeout=60.0)
        break
    except Exception as e:
        last = e
if raw is None:
    raise SystemExit(f'failed to fetch countries.txt: {last}')
Path(sys.argv[3]).write_bytes(raw)
print(f'wrote {sys.argv[3]} bytes={len(raw)}')
PY
    COUNTRIES_TXT=\"\${WORK}/meta/countries.txt\"
  fi
fi

if [[ \"\$SKIP_RMAP\" == \"1\" ]]; then
  mwm_log \"WARN skip region mapping (NAVI_MWM_SKIP_REGION_MAP=1)\"
elif [[ ! -f \"\$REGION_MAP_MOD\" ]]; then
  mwm_log \"WARN skip region mapping (missing \$REGION_MAP_MOD — sync scripts/mwm/lib from scratch)\"
elif [[ -n \"\$BORDERS\" && -d \"\$BORDERS\" && -n \"\$EXTRACTS\" && -d \"\$EXTRACTS\" \
  && -n \"\$REGIONS_JSON\" && -f \"\$REGIONS_JSON\" \
  && -n \"\$REGION_MAP\" \
  && -n \"\$COUNTRIES_TXT\" && -f \"\$COUNTRIES_TXT\" ]]; then
  MAP_OUT=\"\${WORK}/region-mapping\"
  mkdir -p \"\$MAP_OUT\"
  MAP_ARGS=()
  if [[ -f \"\${PUBLISH_ROOT}/current.json\" ]]; then
    PREV_VER=\$(python3 -c \"import json,sys; print(json.load(open(sys.argv[1])).get('version') or '')\" \"\${PUBLISH_ROOT}/current.json\" || true)
    if [[ -n \"\$PREV_VER\" && -f \"\${PUBLISH_ROOT}/\${PREV_VER}/region-migration.json\" ]]; then
      MAP_ARGS+=(--prev-migration \"\${PUBLISH_ROOT}/\${PREV_VER}/region-migration.json\")
    fi
  fi
  if [[ -f \"\${PUBLISH_ROOT}/\${VER}/catalog.json\" ]]; then
    MAP_ARGS+=(--catalog \"\${PUBLISH_ROOT}/\${VER}/catalog.json\")
  elif [[ -f \"\${PUBLISH_ROOT}/current.json\" ]]; then
    MAP_ARGS+=(--catalog \"\${PUBLISH_ROOT}/current.json\")
  fi
  mwm_log \"building region↔leaf mapping + country_groups\"
  \"\$PYTHON\" \"\${SCRIPT_DIR}/build-region-mapping.py\" \\
    --regions-json \"\$REGIONS_JSON\" \\
    --extracts-dir \"\$EXTRACTS\" \\
    --borders-dir \"\$BORDERS\" \\
    --countries-txt \"\$COUNTRIES_TXT\" \\
    --out-dir \"\$MAP_OUT\" \\
    --region-map-out \"\$REGION_MAP\" \\
    --comaps-release \"\$VER\" \\
    \"\${MAP_ARGS[@]}\" || mwm_die \"region mapping failed\"
  cp -a \"\${MAP_OUT}/region-migration.json\" \"\$VERSION_DIR/\"
  cp -a \"\${MAP_OUT}/country_groups.json\" \"\$VERSION_DIR/\"
  [[ -f \"\${MAP_OUT}/region-migration-diff.json\" ]] && \\
    cp -a \"\${MAP_OUT}/region-migration-diff.json\" \"\$VERSION_DIR/\"
else
  mwm_log \"WARN skip region mapping (set NAVI_MWM_BORDERS_DIR, NAVI_MWM_EXTRACTS_DIR, NAVI_MWM_REGIONS_JSON, NAVI_MWM_REGION_MAP; need countries.txt)\"
fi

if [[ \"\$SKIP_PUBLISH\" != \"1\" ]]; then
  \"\${SCRIPT_DIR}/publish-mwm.sh\" --version-dir \"\$VERSION_DIR\" --publish-root \"\$PUBLISH_ROOT\" --sidecars-only
  mwm_log \"published \${VER} -> \${PUBLISH_ROOT}\"
else
  mwm_log \"skip publish; staging ready at \${VERSION_DIR}\"
fi

echo \"DONE finish-comaps-release-postbake version=\${VER} work=\${WORK} publish_root=\${PUBLISH_ROOT} skip_publish=\${SKIP_PUBLISH}\"
"
