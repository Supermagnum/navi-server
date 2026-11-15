"""Poll all enabled DATEX providers. One failure must not stop others."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import List, Mapping, Optional, Sequence

from .auth_types import AuthError, load_auth_material, redact_secrets
from .fetcher import (
    PubFetchResult,
    fetch_publication,
    write_provider_source_json,
    write_provider_status,
)
from .provider_spec import (
    ProviderSpec,
    default_providers_dir,
    load_all_providers,
    provider_is_enabled,
)
from .providers_index import rebuild_providers_index_from_specs

LOG = logging.getLogger("navi.datex.multi_poll")


def _repo_root_from_here() -> Path:
    return Path(__file__).resolve().parents[2]


def poll_provider(
    spec: ProviderSpec,
    *,
    pack_root: Path,
    environ: Mapping[str, str] | None = None,
    jitter: bool = True,
    force: bool = False,
) -> tuple[int, List[PubFetchResult]]:
    """Poll one provider. Returns (exit_code, results). Never raises for pub failures."""
    env = environ if environ is not None else os.environ
    state_dir = pack_root / "datex_state" / spec.id
    status_path = state_dir / "status.json"
    results: List[PubFetchResult] = []

    try:
        auth = load_auth_material(spec, environ=env)
    except AuthError as exc:
        LOG.error("provider=%s auth: %s", spec.id, redact_secrets(str(exc)))
        write_provider_status(
            status_path,
            spec=spec,
            results=[
                PubFetchResult(
                    name="*",
                    status=0,
                    bytes=0,
                    not_modified=False,
                    error=redact_secrets(str(exc)),
                )
            ],
            ok=False,
        )
        return 2, results

    ok_names: list[str] = []
    fatal = False
    for pub in spec.publications:
        if not pub.enabled:
            continue
        # force: clear next_attempt by using now far in future... simpler: pass now and
        # temporarily zero state — for test/force we delete next_attempt via env
        if force:
            sp = state_dir / (
                "".join(c if c.isalnum() or c in "-_" else "_" for c in pub.name) + ".json"
            )
            if sp.is_file():
                try:
                    import json

                    st = json.loads(sp.read_text(encoding="utf-8"))
                    st.pop("next_attempt_unix", None)
                    sp.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")
                except (OSError, json.JSONDecodeError):
                    pass
        try:
            result = fetch_publication(
                spec,
                pub,
                auth,
                pack_root=pack_root,
                state_dir=state_dir,
                jitter=jitter and not force,
            )
        except Exception as exc:  # noqa: BLE001 — isolate providers
            LOG.error(
                "provider=%s pub=%s unexpected: %s",
                spec.id,
                pub.name,
                redact_secrets(str(exc), auth),
            )
            result = PubFetchResult(
                name=pub.name,
                status=0,
                bytes=0,
                not_modified=False,
                error=redact_secrets(str(exc), auth),
            )
        results.append(result)
        if result.error and result.status in (401, 403):
            LOG.error("provider=%s pub=%s %s", spec.id, pub.name, result.error)
            fatal = True
            break
        if result.error and result.status == 0 and (
            "backoff" in (result.error or "") or "interval_hold" in (result.error or "")
        ):
            LOG.info("provider=%s pub=%s %s", spec.id, pub.name, result.error)
            continue
        if result.error:
            LOG.warning("provider=%s pub=%s %s", spec.id, pub.name, result.error)
            continue
        if result.status in (200, 304) or result.not_modified:
            ok_names.append(pub.name)
            LOG.info(
                "provider=%s pub=%s status=%s bytes=%s",
                spec.id,
                pub.name,
                result.status,
                result.bytes,
            )

    if ok_names:
        write_provider_source_json(spec, pack_root, ok_names)
    write_provider_status(status_path, spec=spec, results=results, ok=not fatal and bool(results))
    return (2 if fatal else 0), results


def poll_all(
    *,
    pack_root: Path | None = None,
    providers_dir: Path | None = None,
    environ: Mapping[str, str] | None = None,
    only: Optional[Sequence[str]] = None,
    jitter: bool = True,
    force: bool = False,
) -> int:
    """Poll every enabled provider. Aggregate exit: 0 all ok-ish, 1 partial, 2 auth fatal all."""
    env = environ if environ is not None else os.environ
    root = pack_root or Path(env.get("NAVI_PACK_ROOT", "/media/navi/navi-server/data"))
    pdir = providers_dir or default_providers_dir(_repo_root_from_here())
    specs = load_all_providers(pdir)
    if only:
        want = set(only)
        specs = [s for s in specs if s.id in want or s.public_id in want]
    if not specs:
        LOG.warning("no provider configs in %s", pdir)
        return 0

    codes: list[int] = []
    enabled_specs: list[ProviderSpec] = []
    for spec in specs:
        if not provider_is_enabled(spec, env):
            LOG.info("provider=%s disabled; skip", spec.id)
            continue
        enabled_specs.append(spec)
        LOG.info("provider=%s start (%s pubs)", spec.id, len(spec.publications))
        code, _ = poll_provider(
            spec, pack_root=root, environ=env, jitter=jitter, force=force
        )
        codes.append(code)

    try:
        rebuild_providers_index_from_specs(root, specs, environ=env)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("providers.json rebuild failed: %s", exc)

    if not codes:
        return 0
    if all(c == 0 for c in codes):
        return 0
    if all(c == 2 for c in codes):
        return 2
    return 1
