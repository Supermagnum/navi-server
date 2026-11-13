#!/usr/bin/env bash
# Stream CoMaps official MWMs → Navi sidecars only (default MWM mode).
# Per leaf: download → verify → dump roads+wetlands from MWM → annotate →
# elevation → sidecar bake → record fingerprint → DELETE MWM.
# No planet/continental PBF required.
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
export NAVI_MWM_PYTHON=\"\$PYTHON\"

# Always rebuild so elevation/wetland CLI flags match sources (stale target/ is common).
cargo build -p navi-mwm-sidecar --release --manifest-path \"\${ROOT}/Cargo.toml\"
SIDECAR_BIN=\"\${CARGO_TARGET_DIR}/release/navi-mwm-sidecar\"
\"\$SIDECAR_BIN\" bake --help | grep -q elevation-jsonl || mwm_die \"sidecar binary lacks --elevation-jsonl: \$SIDECAR_BIN\"
export NAVI_MWM_FORCE_REBUILD=\"\${NAVI_MWM_FORCE_REBUILD:-0}\"
\"\${SCRIPT_DIR}/build-dump-mwm-roads.sh\" >/dev/null
DUMP_BIN=\"\${NAVI_MWM_BUILD_DIR}/dump_mwm_roads\"
[[ -x \"\$DUMP_BIN\" ]] || mwm_die \"dump_mwm_roads missing\"
\"\$DUMP_BIN\" --help 2>&1 | grep -q out-wetlands || mwm_die \"dump_mwm_roads lacks --out-wetlands (rebuild required)\"

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
  'countries_txt_sig_verified': info.countries_txt_sig_verified,
  'countries_txt_sig_sha256': info.countries_txt_sig_sha256,
  'countries_txt_pubkey_hex': info.countries_txt_pubkey_hex,
  'leaf_count': len(info.leaves),
  'leaves': {k: {'id': v.id, 'size': v.size, 'sha1_base64': v.sha1_base64,
                 'affiliations': v.affiliations} for k, v in info.leaves.items()},
}))
PY
)

VER=\$(echo \"\$DISCOVERY\" | \"\$PYTHON\" -c 'import json,sys; print(json.load(sys.stdin)[\"version\"])')
SERIES=\$(echo \"\$DISCOVERY\" | \"\$PYTHON\" -c 'import json,sys; print(json.load(sys.stdin)[\"map_series\"])')
SIG_OK=\$(echo \"\$DISCOVERY\" | \"\$PYTHON\" -c 'import json,sys; print(json.load(sys.stdin).get(\"countries_txt_sig_verified\"))')
[[ \"\$SIG_OK\" == \"True\" ]] || mwm_die \"countries.txt.sig not verified\"
WORK=\"\${NAVI_MWM_DOWNLOAD_DIR:-\${NAVI_MWM_MAPS_BUILD}/comaps-release}/\${VER}\"
mkdir -p \"\$WORK/sidecars\" \"\$WORK/tmp\" \"\$WORK/stats\" \"\$WORK/meta\"
echo \"\$DISCOVERY\" >\"\$WORK/meta/discovery.json\"
STATE=\"\$WORK/meta/processed.json\"
[[ -f \"\$STATE\" ]] || echo '{\"release_version\":'\"\$VER\"',\"leaves\":{}}' >\"\$STATE\"

# Restrict discovery leaves to the configured leafset (Nordic by default),
# unless NAVI_MWM_ALL_LEAVES=1 (world run).
if [[ \"\${NAVI_MWM_ALL_LEAVES:-0}\" == \"1\" ]]; then
  # World run: materialize ordered leafset for publish/validate (Nordic→Europe→World).
  WORLD_LEAFSET=\"\$WORK/meta/world-leafset.txt\"
  \"\$PYTHON\" - \"\$SCRIPT_DIR\" \"\$WORK/meta/discovery.json\" \"\$WORLD_LEAFSET\" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'lib'))
from leaf_scheduler import order_leaves
from non_routable import NON_ROUTABLE_LEAVES, is_non_routable_leaf
disc = json.loads(Path(sys.argv[2]).read_text())
all_ids = list(disc['leaves'].keys())
# Sidecar bake skips non-routable overlays (World / WorldCoasts); catalog still lists them.
leaves = order_leaves([leaf for leaf in all_ids if not is_non_routable_leaf(leaf)])
Path(sys.argv[3]).write_text('\\n'.join(leaves) + '\\n')
disc['leaf_count'] = len(leaves)
disc['leafset'] = sys.argv[3]
disc['all_leaves'] = True
disc['non_routable_leaves'] = sorted(NON_ROUTABLE_LEAVES & set(all_ids))
disc['countries_txt_leaf_count'] = len(all_ids)
Path(sys.argv[2]).write_text(json.dumps(disc) + '\\n')
print(f'world leafset {len(leaves)} routable leaves (excluded {disc[\"non_routable_leaves\"]}) -> {sys.argv[3]}')
PY
  export NAVI_MWM_LEAFSET=\"\$WORLD_LEAFSET\"
else
  \"\$PYTHON\" - \"\$WORK/meta/discovery.json\" \"\${NAVI_MWM_LEAFSET}\" <<'PY'
import json, sys
from pathlib import Path
disc_path, leafset_path = sys.argv[1:3]
disc = json.loads(Path(disc_path).read_text())
wanted = set()
for line in Path(leafset_path).read_text().splitlines():
    s = line.strip()
    if s and not s.startswith('#'):
        wanted.add(s)
missing = sorted(wanted - set(disc['leaves']))
if missing:
    raise SystemExit(f'leafset entries missing from countries.txt: {missing[:5]}')
disc['leaves'] = {k: v for k, v in disc['leaves'].items() if k in wanted}
disc['leaf_count'] = len(disc['leaves'])
disc['leafset'] = leafset_path
Path(disc_path).write_text(json.dumps(disc) + '\n')
print(f'leafset filtered to {len(wanted)} leaves')
PY
fi

export NAVI_MWM_HTTP_USER_AGENT=\"\${NAVI_MWM_HTTP_USER_AGENT:-navi-server-mwm/1.0 (+https://github.com/Supermagnum/navi-server)}\"
ATTR_JSON=\"\$WORK/attribution.json\"
\"\$PYTHON\" \"\${SCRIPT_DIR}/build-release-attribution.py\" \
  --discovery \"\$WORK/meta/discovery.json\" \
  --out-json \"\$ATTR_JSON\" \
  --out-md \"\$WORK/ATTRIBUTION.md\"

PARALLEL_ARGS=(
  --work \"\$WORK\"
  --discovery \"\$WORK/meta/discovery.json\"
  --dump-bin \"\$DUMP_BIN\"
  --sidecar-bin \"\$SIDECAR_BIN\"
  --resources-path \"\${NAVI_MWM_COMAPS_ROOT}/data\"
  --attribution \"\$ATTR_JSON\"
)
[[ -n \"\$LEAF_FILTER\" ]] && PARALLEL_ARGS+=(--leaf \"\$LEAF_FILTER\")
[[ \"\$LIMIT\" != \"0\" ]] && PARALLEL_ARGS+=(--limit \"\$LIMIT\")
[[ \"\$SKIP_ANNOTATE\" == \"1\" ]] && PARALLEL_ARGS+=(--skip-annotate)
[[ \"\$SKIP_ELEVATION\" == \"1\" ]] && PARALLEL_ARGS+=(--skip-elevation)
[[ -n \"\${NAVI_MWM_MAPTERHORN_ZOOM:-}\" && \"\${NAVI_MWM_MAPTERHORN_ZOOM}\" != \"0\" ]] && \
  PARALLEL_ARGS+=(--elev-zoom \"\${NAVI_MWM_MAPTERHORN_ZOOM}\")

mwm_log \"parallel leaf bake starting (lock held for whole run)\"
\"\$PYTHON\" \"\${SCRIPT_DIR}/run_parallel_leaves.py\" \"\${PARALLEL_ARGS[@]}\"

STOPPED_EARLY=\$(\"\$PYTHON\" - \"\$WORK/meta/run-summary.json\" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
print(\"1\" if p.is_file() and json.loads(p.read_text()).get(\"stopped_early\") else \"0\")
PY
)
if [[ \"\$STOPPED_EARLY\" == \"1\" ]]; then
  mwm_log \"parallel leaf bake stopped early (graceful stop) — skipping staging/validate/publish\"
  exit 0
fi

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
