"""RadioID mirror entrypoint."""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from plugins.repeater_mirror.publish import (
    PublishError,
    country_meta_from_manifest,
    load_current,
    new_generation_id,
    promote_generation,
    stage_and_validate,
    upsert_source,
    write_current_json,
)

from . import JOIN_KEY, LICENCE_NOTE, SOURCE_ID
from .client import UrlOpener, fetch_country_pages
from .config import Config, is_enabled, load_config, permission_confirmed
from .mapcache import fetch_map_file, load_coords_for_countries
from .normalize import attribution_block, envelope, join_and_normalize
from .secrets import AuthError, load_optional_token

LOG = logging.getLogger("navi.radioid")


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
    fixture_api: Optional[Mapping[str, List[dict]]] = None,
    fixture_map_by_locator: Optional[Mapping[int, dict]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    now_unix: Optional[int] = None,
    generation: Optional[str] = None,
) -> int:
    env = dict(environ) if environ is not None else dict(os.environ)
    dry = env.get("NAVI_RADIOID_DRY_RUN", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    if not dry:
        if not is_enabled(env):
            LOG.info("radioid disabled (NAVI_RADIOID_ENABLED!=1); inert exit")
            return 0
        if not permission_confirmed(env):
            LOG.info(
                "radioid: RADIOID_PERMISSION_CONFIRMED!=1 — "
                "refusing to contact RadioID or publish. "
                "Set this only after written permission exists."
            )
            return 0

    if cfg is None:
        cfg = load_config(env)

    if not dry:
        if not cfg.enabled or not cfg.permission_confirmed:
            LOG.info("radioid: enabled/permission gate closed; inert exit")
            return 0

    try:
        token = load_optional_token(environ=env, secrets_file=cfg.secrets_file)
    except AuthError as exc:
        LOG.error("%s", exc)
        return 2

    fetched_unix = int(now_unix if now_unix is not None else time.time())
    gen = generation or new_generation_id()
    stage_root = cfg.work_dir / "staging" / gen
    if stage_root.exists():
        shutil.rmtree(stage_root)
    stage_root.mkdir(parents=True, exist_ok=True)

    # Map once per run
    if fixture_map_by_locator is not None:
        map_by_loc = dict(fixture_map_by_locator)
    else:
        try:
            map_path = fetch_map_file(cfg, token, opener=opener)
            map_by_loc = load_coords_for_countries(map_path, cfg.countries)
        except Exception as exc:  # noqa: BLE001
            LOG.error("map.json failed: %s — keeping last good data", exc)
            return 1

    LOG.info(
        "map cache countries=%s locators=%s join_key=%s",
        ",".join(cfg.countries),
        len(map_by_loc),
        JOIN_KEY,
    )

    countries_meta: List[Dict[str, Any]] = []
    any_failure = False
    attr = attribution_block()

    for country in cfg.countries:
        slug = cfg.country_slugs.get(country, country.lower())
        # Per-country map subset
        country_map = {
            loc: m
            for loc, m in map_by_loc.items()
            if str(m.get("country") or "").lower() == country.lower()
        }
        if fixture_api is not None:
            from .client import FetchOutcome

            outcome = FetchOutcome(
                country=country, status=200, rows=list(fixture_api.get(country, []))
            )
        else:
            outcome = fetch_country_pages(
                cfg, country, token, opener=opener, sleeper=sleeper
            )

        if outcome.error or not outcome.rows:
            any_failure = True
            LOG.error(
                "country=%s fetch failed status=%s error=%s — keeping last good",
                country,
                outcome.status,
                outcome.error or "empty_result",
            )
            continue

        records, stats = join_and_normalize(outcome.rows, country_map)
        LOG.info(
            "country=%s join matched=%s api_only=%s map_only=%s",
            country,
            stats["matched"],
            stats["api_only"],
            stats["map_only"],
        )
        env_doc = envelope(
            country=country,
            country_slug=slug,
            generation=gen,
            fetched_unix=fetched_unix,
            records=records,
            join_stats=stats,
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
                extra_manifest=stats,
            )
            promote_generation(
                cfg.publish_root,
                SOURCE_ID,
                slug,
                gen,
                stage_dir,
                keep_generations=cfg.keep_generations,
            )
            meta = country_meta_from_manifest(
                manifest, source=SOURCE_ID, country_slug=slug, generation=gen
            )
            countries_meta.append(meta)
        except PublishError as exc:
            any_failure = True
            LOG.error("country=%s publish refused: %s", country, exc)

    if not countries_meta:
        LOG.error("no radioid countries published; current.json unchanged")
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
    LOG.info("wrote current.json source=%s countries=%s", SOURCE_ID, len(countries_meta))
    return 1 if any_failure else 0


def main(argv: Optional[List[str]] = None) -> int:
    _configure_logging()
    p = argparse.ArgumentParser(description="Navi RadioID mirror job")
    p.add_argument("--dry-run-fixtures", metavar="DIR")
    p.add_argument("--publish-root", metavar="DIR")
    args = p.parse_args(argv)
    env = dict(os.environ)
    if args.dry_run_fixtures:
        env["NAVI_RADIOID_DRY_RUN"] = "1"
        env.setdefault("NAVI_RADIOID_ENABLED", "1")
        env.setdefault("RADIOID_PERMISSION_CONFIRMED", "1")
        fix = Path(args.dry_run_fixtures)
        cfg = load_config(env)
        api_path = fix / "Norway.api.synthetic.json"
        map_path = fix / "Norway.map.synthetic.json"
        if not api_path.is_file() or not map_path.is_file():
            LOG.error("need Norway.api.synthetic.json and Norway.map.synthetic.json in %s", fix)
            return 2
        api_doc = json.loads(api_path.read_text(encoding="utf-8"))
        map_doc = json.loads(map_path.read_text(encoding="utf-8"))
        api_rows = api_doc.get("results") if isinstance(api_doc, dict) else api_doc
        markers = map_doc.get("markers") if isinstance(map_doc, dict) else map_doc
        map_by = {}
        for m in markers or []:
            try:
                map_by[int(m["locator"])] = m
            except (KeyError, TypeError, ValueError):
                continue
        if args.publish_root:
            pub = Path(args.publish_root)
            pub.mkdir(parents=True, exist_ok=True)
            cfg = replace(cfg, publish_root=pub, work_dir=pub.parent / "radioid-work")
        return run_mirror(
            cfg,
            environ=env,
            fixture_api={"Norway": list(api_rows or [])},
            fixture_map_by_locator=map_by,
        )
    return run_mirror(environ=env)


if __name__ == "__main__":
    sys.exit(main())
