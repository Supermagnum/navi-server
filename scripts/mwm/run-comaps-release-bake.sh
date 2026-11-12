#!/usr/bin/env bash
# Stream CoMaps official MWMs → Navi sidecars only (default MWM mode).
# Per leaf: download → verify → dump roads → wetland annotate → elevation →
# sidecar bake → record fingerprint → DELETE MWM.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

FORCE=0
PUBLISH_ROOT="${NAVI_MWM_PUBLISHED_ROOT}"
SKIP_PUBLISH=0
SKIP_VALIDATE=0
SKIP_ANNOTATE="${NAVI_MWM_SKIP_ANNOTATE:-0}"
SKIP_ELEVATION="${NAVI_MWM_SKIP_ELEVATION:-0}"
LEAF_FILTER=""
LIMIT=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --publish-root) PUBLISH_ROOT="$2"; shift 2 ;;
    --skip-publish) SKIP_PUBLISH=1; shift ;;
    --skip-validate) SKIP_VALIDATE=1; shift ;;
    --skip-annotate) SKIP_ANNOTATE=1; shift ;;
    --skip-elevation) SKIP_ELEVATION=1; shift ;;
    --leaf) LEAF_FILTER="$2"; shift 2 ;;
    --limit) LIMIT="$2"; shift 2 ;;
    *) mwm_die "unknown arg: $1" ;;
  esac
done

if [[ "${NAVI_MWM_ENABLED}" != "1" && "$FORCE" != "1" ]]; then
  mwm_die "NAVI_MWM_ENABLED=0 (pass --force for scratch)"
fi

ROOT="$(mwm_repo_root)"
# Pin repo target — never inherit a sandbox/cache CARGO_TARGET_DIR (stale bake CLI).
export CARGO_TARGET_DIR="${ROOT}/target"
PYTHON="${NAVI_MWM_PYTHON:-python3}"
if [[ -x /media/navi/haaken-scratch/mwm-comaps-scratch/venv/bin/python ]]; then
  PYTHON="${NAVI_MWM_PYTHON:-/media/navi/haaken-scratch/mwm-comaps-scratch/venv/bin/python}"
fi

mwm_with_lock bash -c "
set -euo pipefail
source '${SCRIPT_DIR}/lib/common.sh'
mwm_load_defaults
ROOT='${ROOT}'
SCRIPT_DIR='${SCRIPT_DIR}'
PYTHON='${PYTHON}'
PUBLISH_ROOT='${PUBLISH_ROOT}'
SKIP_PUBLISH='${SKIP_PUBLISH}'
SKIP_VALIDATE='${SKIP_VALIDATE}'
SKIP_ANNOTATE='${SKIP_ANNOTATE}'
SKIP_ELEVATION='${SKIP_ELEVATION}'
LEAF_FILTER='${LEAF_FILTER}'
LIMIT='${LIMIT}'
FORCE='${FORCE}'
export CARGO_TARGET_DIR='${ROOT}/target'

# Always rebuild so elevation/wetland CLI flags match sources (stale target/ is common).
cargo build -p navi-mwm-sidecar --release --manifest-path \"\${ROOT}/Cargo.toml\"
SIDECAR_BIN=\"\${CARGO_TARGET_DIR}/release/navi-mwm-sidecar\"
\"\$SIDECAR_BIN\" bake --help | grep -q elevation-jsonl || mwm_die \"sidecar binary lacks --elevation-jsonl: \$SIDECAR_BIN\"
\"\${SCRIPT_DIR}/build-dump-mwm-roads.sh\" >/dev/null
DUMP_BIN=\"\${NAVI_MWM_BUILD_DIR}/dump_mwm_roads\"
[[ -x \"\$DUMP_BIN\" ]] || mwm_die \"dump_mwm_roads missing\"

DISCOVERY=\$(\"\$PYTHON\" - \"\$SCRIPT_DIR\" <<'PY'
import json, os, sys
sys.path.insert(0, os.path.join(sys.argv[1], 'lib'))
from comaps_release import discover_release, DEFAULT_METASERVER, DEFAULT_MAP_SERIES
meta = os.environ.get('NAVI_MWM_METASERVER', DEFAULT_METASERVER)
series = os.environ.get('NAVI_MWM_MAP_SERIES', DEFAULT_MAP_SERIES)
ver = os.environ.get('NAVI_MWM_COMAPS_VERSION')
info = discover_release(
    metaserver=meta,
    map_series=series,
    data_version=int(ver) if ver else None,
)
print(json.dumps({
  'map_series': info.map_series,
  'version': info.version,
  'metaserver_url': info.metaserver_url,
  'mirrors': info.mirrors,
  'countries_txt_sha256': info.countries_txt_sha256,
  'countries_txt_bytes': info.countries_txt_bytes,
  'leaf_count': len(info.leaves),
  'leaves': {k: {'id': v.id, 'size': v.size, 'sha1_base64': v.sha1_base64,
                 'affiliations': v.affiliations} for k, v in info.leaves.items()},
}))
PY
)

VER=\$(echo \"\$DISCOVERY\" | \"\$PYTHON\" -c 'import json,sys; print(json.load(sys.stdin)[\"version\"])')
SERIES=\$(echo \"\$DISCOVERY\" | \"\$PYTHON\" -c 'import json,sys; print(json.load(sys.stdin)[\"map_series\"])')
WORK=\"\${NAVI_MWM_DOWNLOAD_DIR:-\${NAVI_MWM_MAPS_BUILD}/comaps-release}/\${VER}\"
mkdir -p \"\$WORK/sidecars\" \"\$WORK/tmp\" \"\$WORK/stats\" \"\$WORK/meta\"
echo \"\$DISCOVERY\" >\"\$WORK/meta/discovery.json\"
STATE=\"\$WORK/meta/processed.json\"
[[ -f \"\$STATE\" ]] || echo '{\"release_version\":'\"\$VER\"',\"leaves\":{}}' >\"\$STATE\"

export NAVI_MWM_HTTP_USER_AGENT=\"\${NAVI_MWM_HTTP_USER_AGENT:-navi-server-mwm/1.0 (+https://github.com/Supermagnum/navi-server)}\"
ATTR_JSON=\"\$WORK/attribution.json\"
\"\$PYTHON\" \"\${SCRIPT_DIR}/build-release-attribution.py\" \
  --discovery \"\$WORK/meta/discovery.json\" \
  --out-json \"\$ATTR_JSON\" \
  --out-md \"\$WORK/ATTRIBUTION.md\"

PROCESSED=0
while IFS= read -r leaf; do
  [[ -n \"\$leaf\" ]] || continue
  if [[ -n \"\$LEAF_FILTER\" && \"\$leaf\" != \"\$LEAF_FILTER\" ]]; then
    continue
  fi
  if [[ \"\$LIMIT\" != \"0\" && \"\$PROCESSED\" -ge \"\$LIMIT\" ]]; then
    break
  fi
  # Skip if already processed for this release
  ALREADY=\$(\"\$PYTHON\" - \"\$STATE\" \"\$leaf\" <<'PY'
import json,sys
st=json.load(open(sys.argv[1]))
print('1' if sys.argv[2] in st.get('leaves',{}) else '0')
PY
)
  if [[ \"\$ALREADY\" == \"1\" ]]; then
    mwm_log \"skip already processed \${leaf}\"
    continue
  fi

  mwm_log \"leaf start \${leaf}\"
  MWM=\"\$WORK/tmp/\${leaf}.mwm\"
  ROADS=\"\$WORK/tmp/\${leaf}.roads.jsonl\"
  WJSON=\"\$WORK/tmp/\${leaf}.wetland.jsonl\"
  BJSON=\"\$WORK/tmp/\${leaf}.boardwalk.jsonl\"
  EJSON=\"\$WORK/tmp/\${leaf}.elevation.jsonl\"
  OUT=\"\$WORK/sidecars/\${leaf}.navi-sidecar.v1.zst\"
  ESTATS=\"\$WORK/stats/\${leaf}.elevation.json\"

  cleanup_leaf() {
    rm -f \"\$MWM\" \"\$MWM.part\" \"\$ROADS\" \"\$WJSON\" \"\$BJSON\" \"\$EJSON\"
  }
  trap cleanup_leaf EXIT

  \"\$PYTHON\" \"\${SCRIPT_DIR}/download_comaps_leaf.py\" \
    --discovery \"\$WORK/meta/discovery.json\" \
    --leaf \"\$leaf\" \
    --out \"\$MWM\"

  \"\$DUMP_BIN\" --mwm \"\$MWM\" --resources_path \"\${NAVI_MWM_COMAPS_ROOT}/data\" --out \"\$ROADS\"

  if [[ \"\$SKIP_ANNOTATE\" == \"1\" ]]; then
    : >\"\$WJSON\"; : >\"\$BJSON\"
  else
    PBF=\"\${NAVI_MWM_PLANET_PBF:-}\"
    [[ -n \"\$PBF\" && -f \"\$PBF\" ]] || mwm_die \"NAVI_MWM_PLANET_PBF required for annotate (or --skip-annotate)\"
    BORDER=\"\${NAVI_MWM_COMAPS_ROOT}/data/borders/\${leaf}.poly\"
    \"\$PYTHON\" \"\${SCRIPT_DIR}/annotate-wetland-edges.py\" \
      --pbf \"\$PBF\" \
      --roads-jsonl \"\$ROADS\" \
      --border-poly \"\$BORDER\" \
      --out-wetland \"\$WJSON\" \
      --out-boardwalk \"\$BJSON\"
  fi

  if [[ \"\$SKIP_ELEVATION\" == \"1\" ]]; then
    : >\"\$EJSON\"
  else
    \"\$PYTHON\" \"\${SCRIPT_DIR}/build-elevation-overrides.py\" \
      --roads-jsonl \"\$ROADS\" \
      --out \"\$EJSON\" \
      --cache-dir \"\${NAVI_MWM_MAPTERHORN_CACHE}\" \
      --cache-max-bytes \"\${NAVI_MWM_MAPTERHORN_CACHE_MAX_BYTES}\" \
      --threshold-m \"\${NAVI_MWM_ELEV_THRESHOLD_M}\" \
      --stats-out \"\$ESTATS\"
  fi

  TAG=\"comaps-release/\${SERIES}\"
  SHA=\"\${VER}\"
  \"\$SIDECAR_BIN\" bake \
    --mwm \"\$MWM\" \
    --wetland-jsonl \"\$WJSON\" \
    --boardwalk-jsonl \"\$BJSON\" \
    --elevation-jsonl \"\$EJSON\" \
    --attribution \"\$ATTR_JSON\" \
    --generator-tag \"\$TAG\" \
    --generator-sha \"\$SHA\" \
    --ambiguous-threshold \"\${NAVI_MWM_AMBIGUOUS_THRESHOLD}\" \
    --out \"\$OUT\" | tee \"\$WORK/stats/\${leaf}.sidecar.json\"

  \"\$PYTHON\" - \"\$STATE\" \"\$leaf\" \"\$MWM\" \"\$OUT\" \"\$WORK/meta/discovery.json\" <<'PY'
import json, hashlib, sys
from pathlib import Path
state_path, leaf, mwm, out, disc_path = sys.argv[1:6]
st = json.load(open(state_path))
disc = json.load(open(disc_path))
meta = disc['leaves'][leaf]
h = hashlib.sha256(); n=0
with open(mwm,'rb') as f:
  for c in iter(lambda: f.read(1<<20), b''):
    h.update(c); n += len(c)
sh = hashlib.sha256()
with open(out,'rb') as f:
  for c in iter(lambda: f.read(1<<20), b''):
    sh.update(c)
st.setdefault('leaves',{})[leaf] = {
  'comaps_bytes': meta['size'],
  'comaps_sha1_base64': meta['sha1_base64'],
  'mwm_bytes': n,
  'mwm_sha256': h.hexdigest(),
  'sidecar_bytes': Path(out).stat().st_size,
  'sidecar_sha256': sh.hexdigest(),
}
st['release_version'] = disc['version']
Path(state_path).write_text(json.dumps(st, indent=2)+'\n')
print('recorded', leaf)
PY

  cleanup_leaf
  trap - EXIT
  PROCESSED=\$((PROCESSED+1))
  mwm_log \"leaf done \${leaf} (processed=\${PROCESSED})\"
done < <(mwm_read_leafset)

# Stage version dir for publish (sidecars only)
VERSION_DIR=\"\$WORK/publish-staging/\${VER}\"
rm -rf \"\$VERSION_DIR\"
mkdir -p \"\$VERSION_DIR\"
cp -a \"\$WORK/sidecars/\"*.navi-sidecar.v1.zst \"\$VERSION_DIR/\" 2>/dev/null || true
cp -a \"\$ATTR_JSON\" \"\$VERSION_DIR/attribution.json\"
cp -a \"\$WORK/ATTRIBUTION.md\" \"\$VERSION_DIR/ATTRIBUTION.md\"
cp -a \"\$WORK/meta/discovery.json\" \"\$VERSION_DIR/discovery.json\"
cp -a \"\$STATE\" \"\$VERSION_DIR/processed.json\"

if [[ \"\$SKIP_VALIDATE\" != \"1\" ]]; then
  \"\${SCRIPT_DIR}/validate-comaps-release.sh\" --version-dir \"\$VERSION_DIR\" --work-dir \"\$WORK\" || mwm_die \"validate failed\"
fi
if [[ \"\$SKIP_PUBLISH\" != \"1\" ]]; then
  \"\${SCRIPT_DIR}/publish-mwm.sh\" --version-dir \"\$VERSION_DIR\" --publish-root \"\$PUBLISH_ROOT\" --sidecars-only
fi
echo \"DONE comaps-release version=\${VER} work=\${WORK} publish_root=\${PUBLISH_ROOT}\"
"
