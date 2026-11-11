#!/usr/bin/env bash
# Orchestrate Nordic MWM bake: generator → sidecars → validate → publish.
# Off by default (NAVI_MWM_ENABLED=0). Use --force for scratch.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

FORCE=0
PUBLISH_ROOT="${NAVI_MWM_PUBLISHED_ROOT}"
SKIP_PUBLISH=0
SKIP_VALIDATE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --publish-root) PUBLISH_ROOT="$2"; shift 2 ;;
    --skip-publish) SKIP_PUBLISH=1; shift ;;
    --skip-validate) SKIP_VALIDATE=1; shift ;;
    *) mwm_die "unknown arg: $1" ;;
  esac
done

if [[ "${NAVI_MWM_ENABLED}" != "1" && "$FORCE" != "1" ]]; then
  mwm_die "NAVI_MWM_ENABLED=0 (pass --force for scratch)"
fi

FORCE_FLAG=()
[[ "$FORCE" == "1" ]] && FORCE_FLAG+=(--force)

mwm_with_lock bash -c "
  set -euo pipefail
  source '${SCRIPT_DIR}/lib/common.sh'
  mwm_load_defaults
  '${SCRIPT_DIR}/build-generator.sh'
  VERSION_DIR=\"\$('${SCRIPT_DIR}/run-generator.sh' --skip-lock ${FORCE_FLAG[*]} | tail -1)\"
  echo \"VERSION_DIR=\$VERSION_DIR\"
  '${SCRIPT_DIR}/build-sidecars.sh' --version-dir \"\$VERSION_DIR\"
  if [[ '${SKIP_VALIDATE}' != '1' ]]; then
    '${SCRIPT_DIR}/validate-release.sh' --version-dir \"\$VERSION_DIR\"
  fi
  if [[ '${SKIP_PUBLISH}' != '1' ]]; then
    '${SCRIPT_DIR}/publish-mwm.sh' --version-dir \"\$VERSION_DIR\" --publish-root '${PUBLISH_ROOT}'
  fi
  echo \"DONE version=\$VERSION_DIR publish_root=${PUBLISH_ROOT}\"
"
