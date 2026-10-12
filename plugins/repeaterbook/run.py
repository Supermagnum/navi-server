"""RepeaterBook mirror entrypoint: gates, fetch, normalize, atomic publish."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from . import LICENCE_NOTE, SOURCE_ID
from .client import FetchOutcome, UrlOpener, fetch_country
from .config import Config, is_enabled, load_config, permission_confirmed
from .normalize import attribution_block, envelope, normalize_rows
from .publish import (
    PublishError,
    country_meta_from_manifest,
    load_current,
    new_generation_id,
    promote_generation,
    stage_and_validate,
    upsert_source,
    write_current_json,
)
from .secrets import AuthError, load_app_token, redact_secrets

LOG = logging.getLogger("navi.repeaterbook")


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )


def run_mirror(
    cfg: Optional[Config] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    opener: Optional[UrlOpener] = None,
    fixture_rows: Optional[Mapping[str, List[dict]]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    now_unix: Optional[int] = None,
    generation: Optional[str] = None,
) -> int:
    env = dict(environ) if environ is not None else dict(os.environ)
    dry = env.get("NAVI_REPEATERBOOK_DRY_RUN", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    if not dry:
        if not is_enabled(env):
            LOG.info(
                "repeaterbook disabled (NAVI_REPEATERBOOK_ENABLED!=1); inert exit"
            )
            return 0
        if not permission_confirmed(env):
            LOG.info(
                "repeaterbook: REPEATERBOOK_PERMISSION_CONFIRMED!=1 — "
                "refusing to contact RepeaterBook or publish. "
                "Set this only after written permission exists."
            )
            return 0

    if cfg is None:
        cfg = load_config(env)

    if not dry:
        if not cfg.enabled:
            LOG.info("repeaterbook disabled after config load; inert exit")
            return 0
        if not cfg.permission_confirmed:
            LOG.info(
                "repeaterbook: permission not confirmed after config load; inert exit"
            )
            return 0

    token = None
    if fixture_rows is None:
        try:
            token = load_app_token(environ=env, secrets_file=cfg.secrets_file)
        except AuthError as exc:
            LOG.error("%s", exc)
            return 2

    fetched_unix = int(now_unix if now_unix is not None else time.time())
    gen = generation or new_generation_id()
    stage_root = cfg.work_dir / "staging" / gen
    if stage_root.exists():
        import shutil

        shutil.rmtree(stage_root)
    stage_root.mkdir(parents=True, exist_ok=True)

    countries_meta: List[Dict[str, Any]] = []
    any_failure = False
    attr = attribution_block()

    for country in cfg.countries:
        slug = cfg.country_slugs.get(country, country.lower())
        if fixture_rows is not None:
            raw_rows = list(fixture_rows.get(country, []))
            outcome = FetchOutcome(country=country, status=200, rows=raw_rows)
        else:
            assert token is not None
            outcome = fetch_country(
                cfg, country, token, opener=opener, sleeper=sleeper
            )

        if outcome.error or not outcome.rows:
            any_failure = True
            err = outcome.error or "empty_result"
            if token is not None:
                err = redact_secrets(err, token.token)
            LOG.error(
                "country=%s fetch failed status=%s error=%s — keeping last good data",
                country,
                outcome.status,
                err,
            )
            continue

        records = normalize_rows(outcome.rows)
        env_doc = envelope(
            country=country,
            country_slug=slug,
            generation=gen,
            fetched_unix=fetched_unix,
            records=records,
        )
        try:
            stage_dir, manifest = stage_and_validate(
                cfg.publish_root,
                source=SOURCE_ID,
                country=country,
                country_slug=slug,
                generation=gen,
                fetched_unix=fetched_unix,
                envelope=env_doc,
                stage_root=stage_root,
                attribution=attr,
                licence=LICENCE_NOTE,
                ratio_min=cfg.count_ratio_min,
                ratio_max=cfg.count_ratio_max,
            )
            promote_generation(
                cfg.publish_root,
                SOURCE_ID,
                slug,
                gen,
                stage_dir,
                keep_generations=cfg.keep_generations,
            )
            countries_meta.append(
                country_meta_from_manifest(
                    manifest, source=SOURCE_ID, country_slug=slug, generation=gen
                )
            )
            LOG.info(
                "country=%s slug=%s published generation=%s records=%s",
                country,
                slug,
                gen,
                manifest.get("record_count"),
            )
        except PublishError as exc:
            any_failure = True
            LOG.error(
                "country=%s publish refused: %s — keeping last good data",
                country,
                exc,
            )

    if not countries_meta:
        LOG.error("no countries published this run; current.json left unchanged")
        return 1

    doc = load_current(cfg.publish_root)
    doc = upsert_source(
        doc,
        source=SOURCE_ID,
        attribution=attr,
        licence=LICENCE_NOTE,
        countries=countries_meta,
        generation=gen,
        fetched_unix=fetched_unix,
    )
    write_current_json(cfg.publish_root, doc)
    LOG.info(
        "wrote current.json generation=%s source=%s countries=%s",
        gen,
        SOURCE_ID,
        len(countries_meta),
    )
    return 1 if any_failure else 0


def main(argv: Optional[List[str]] = None) -> int:
    _configure_logging()
    p = argparse.ArgumentParser(description="Navi RepeaterBook mirror job")
    p.add_argument("--dry-run-fixtures", metavar="DIR")
    p.add_argument("--publish-root", metavar="DIR")
    args = p.parse_args(argv)

    env = dict(os.environ)
    if args.dry_run_fixtures:
        from .client import parse_export_payload

        env["NAVI_REPEATERBOOK_DRY_RUN"] = "1"
        env.setdefault("NAVI_REPEATERBOOK_ENABLED", "1")
        env.setdefault("REPEATERBOOK_PERMISSION_CONFIRMED", "1")
        fix_dir = Path(args.dry_run_fixtures)
        fixture_rows: Dict[str, List[dict]] = {}
        cfg = load_config(env)
        for country in cfg.countries:
            slug = cfg.country_slugs.get(country, country.lower())
            candidates = [
                fix_dir / f"{country}.json",
                fix_dir / f"{country}.synthetic.json",
                fix_dir / f"{slug}.json",
                fix_dir / f"{slug}.synthetic.json",
            ]
            path = next((c for c in candidates if c.is_file()), None)
            if path is None:
                LOG.error("missing fixture for country=%s under %s", country, fix_dir)
                return 2
            payload = json.loads(path.read_text(encoding="utf-8"))
            fixture_rows[country] = parse_export_payload(payload)

        if args.publish_root:
            publish_root = Path(args.publish_root)
            publish_root.mkdir(parents=True, exist_ok=True)
            cfg = replace(
                cfg,
                publish_root=publish_root,
                work_dir=publish_root.parent / "repeaterbook-work",
            )
        return run_mirror(cfg, environ=env, fixture_rows=fixture_rows)

    return run_mirror(environ=env)


if __name__ == "__main__":
    sys.exit(main())
