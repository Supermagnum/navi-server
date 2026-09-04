"""Poll entrypoint: enable check first, then credentials, then fetches."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from .auth import AuthError, load_credentials
from .client import UrlOpener, fetch_endpoint, write_source_metadata
from .config import Config, is_enabled, load_config

LOG = logging.getLogger("navi.datex_npra")


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )


def poll_once(
    cfg: Optional[Config] = None,
    *,
    environ: Optional[dict] = None,
    opener: Optional[UrlOpener] = None,
    jitter: bool = True,
) -> int:
    """Run one poll cycle. Returns process exit code.

    When disabled, returns 0 without reading credentials or opening network.
    """
    env = environ if environ is not None else os.environ

    # HARD GATE — must remain the first behavioural check.
    if not is_enabled(env):
        LOG.info("datex_npra disabled (NAVI_DATEX_NPRA_ENABLED!=1); inert exit")
        return 0

    if cfg is None:
        cfg = load_config(env)

    if not cfg.enabled:
        LOG.info("datex_npra disabled after config load; inert exit")
        return 0

    try:
        creds = load_credentials(environ=env, secrets_file=cfg.secrets_file)
    except AuthError as exc:
        LOG.error("%s", exc)
        return 2

    cfg.publish_dir.mkdir(parents=True, exist_ok=True)
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)

    ok_endpoints: list[str] = []
    fatal_auth = False
    for endpoint in cfg.endpoints:
        try:
            result = fetch_endpoint(
                cfg, endpoint, creds, opener=opener, jitter=jitter
            )
        except AuthError as exc:
            LOG.error("%s", exc)
            fatal_auth = True
            break
        if result.error and result.status == 0 and "backoff" in (result.error or ""):
            LOG.info(
                "endpoint=%s status=backoff detail=%s",
                endpoint,
                result.error,
            )
            continue
        if result.error:
            LOG.warning(
                "endpoint=%s status=%s bytes=%s error=%s",
                endpoint,
                result.status,
                result.bytes,
                result.error,
            )
            continue
        LOG.info(
            "endpoint=%s status=%s bytes=%s not_modified=%s",
            endpoint,
            result.status,
            result.bytes,
            result.not_modified,
        )
        if (cfg.publish_dir / f"{endpoint}.xml").is_file():
            ok_endpoints.append(endpoint)

    if fatal_auth:
        return 2

    if ok_endpoints:
        write_source_metadata(cfg, ok_endpoints)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(description="Poll NPRA DATEX snapshots (if enabled)")
    parser.add_argument(
        "--pack-root",
        type=Path,
        default=None,
        help="Override NAVI_PACK_ROOT",
    )
    parser.add_argument(
        "--no-jitter",
        action="store_true",
        help="Disable sleep jitter (tests / manual)",
    )
    args = parser.parse_args(argv)

    # Enable check before any pack-root side effects beyond env read.
    if not is_enabled(os.environ):
        LOG.info("datex_npra disabled (NAVI_DATEX_NPRA_ENABLED!=1); inert exit")
        return 0

    cfg = load_config(pack_root=args.pack_root)
    return poll_once(cfg, jitter=not args.no_jitter)


if __name__ == "__main__":
    sys.exit(main())
