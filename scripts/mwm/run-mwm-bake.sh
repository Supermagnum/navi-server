#!/usr/bin/env bash
# Orchestrate MWM bake. Default mode: comaps-release (sidecars only).
# NAVI_MWM_MODE=generate keeps the Nordic generator path (disabled by default).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
mwm_load_defaults

FORCE=0
PUBLISH_ROOT="${NAVI_MWM_PUBLISHED_ROOT}"
SKIP_PUBLISH=0
SKIP_VALIDATE=0
EXTRA=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; EXTRA+=(--force); shift ;;
    --publish-root) PUBLISH_ROOT="$2"; EXTRA+=(--publish-root "$2"); shift 2 ;;
    --skip-publish) SKIP_PUBLISH=1; EXTRA+=(--skip-publish); shift ;;
    --skip-validate) SKIP_VALIDATE=1; EXTRA+=(--skip-validate); shift ;;
    --leaf|--limit|--skip-annotate|--skip-elevation)
      # Forward comaps-release-only flags
      if [[ "$1" == --leaf || "$1" == --limit ]]; then
        EXTRA+=("$1" "$2"); shift 2
      else
        EXTRA+=("$1"); shift
      fi
      ;;
    *) mwm_die "unknown arg: $1" ;;
  esac
done

if [[ "${NAVI_MWM_ENABLED}" != "1" && "$FORCE" != "1" ]]; then
  mwm_die "NAVI_MWM_ENABLED=0 (pass --force for scratch)"
fi

case "${NAVI_MWM_MODE}" in
  comaps-release)
    exec "${SCRIPT_DIR}/run-comaps-release-bake.sh" "${EXTRA[@]}"
    ;;
  generate)
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
    ;;
  *)
    mwm_die "unknown NAVI_MWM_MODE=${NAVI_MWM_MODE} (expected comaps-release|generate)"
    ;;
esac
