"""OSM repeaters extract entrypoint."""

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
from typing import Any, Dict, List, Mapping, Optional, Tuple

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

from . import LICENCE_NOTE, SOURCE_ID
from .config import Config, is_enabled, load_config
from .countries import CountryPlan, plan_countries
from .extract import OsmiumRunner, extract_country
from .normalize import attribution_block, content_sha256, envelope

LOG = logging.getLogger("navi.osm_repeaters")


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )


def _live_content_sha256(publish_root: Path, slug: str) -> Optional[str]:
    country_dir = publish_root / SOURCE_ID / slug
    if not country_dir.is_dir():
        return None
    gens = sorted(
        [p for p in country_dir.iterdir() if p.is_dir() and not p.name.startswith(".")],
        key=lambda p: p.name,
        reverse=True,
    )
    for g in gens:
        man = g / "manifest.json"
        if not man.is_file():
            continue
        try:
            m = json.loads(man.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        h = m.get("content_sha256")
        if isinstance(h, str) and h:
            return h
    return None


def run_extract(
    cfg: Optional[Config] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    runner: Optional[OsmiumRunner] = None,
    fixture_by_slug: Optional[Mapping[str, Mapping[str, Tuple[Path, Path]]]] = None,
    now_unix: Optional[int] = None,
    generation: Optional[str] = None,
) -> int:
    env = dict(environ) if environ is not None else dict(os.environ)
    dry = env.get("NAVI_OSM_REPEATERS_DRY_RUN", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    if not dry:
        if not is_enabled(env):
            LOG.info("osm_repeaters disabled (NAVI_OSM_REPEATERS_ENABLED!=1); inert exit")
            return 0

    if cfg is None:
        cfg = load_config(env)

    if not dry and not cfg.enabled:
        LOG.info("osm_repeaters: enabled gate closed; inert exit")
        return 0

    fetched_unix = int(now_unix if now_unix is not None else time.time())
    gen = generation or new_generation_id()
    stage_root = cfg.work_dir / "staging" / gen
    if stage_root.exists():
        shutil.rmtree(stage_root)
    stage_root.mkdir(parents=True, exist_ok=True)

    conf_paths = [cfg.regions_conf, cfg.regions_planet_conf]
    if fixture_by_slug is not None:
        # Fixture mode: build synthetic plans from override or fixture keys
        plans: List[CountryPlan] = []
        override = cfg.countries_override
        if override:
            for slug, prefix in override.items():
                plans.append(
                    CountryPlan(
                        slug=slug,
                        prefix=prefix,
                        country_name=prefix.rsplit("/", 1)[-1].replace("-", " ").title(),
                        regions=tuple(),
                    )
                )
        else:
            for slug in fixture_by_slug:
                plans.append(
                    CountryPlan(
                        slug=slug,
                        prefix=f"fixture/{slug}",
                        country_name=slug.upper(),
                        regions=tuple(),
                    )
                )
    else:
        plans = plan_countries(
            conf_paths=conf_paths,
            extracts_dir=cfg.extracts_dir,
            countries_override=cfg.countries_override,
        )

    countries_meta: List[Dict[str, Any]] = []
    any_failure = False
    attr = attribution_block()
    published_any = False

    for plan in plans:
        LOG.info("country slug=%s prefix=%s regions=%s", plan.slug, plan.prefix, len(plan.regions))
        try:
            fix = fixture_by_slug.get(plan.slug) if fixture_by_slug else None
            records, networks, stats = extract_country(
                plan,
                work_dir=cfg.work_dir,
                runner=runner,
                fixture_opl=fix,
            )
        except FileNotFoundError as exc:
            any_failure = True
            LOG.error("country=%s missing inputs: %s — keeping last good", plan.slug, exc)
            continue
        except Exception as exc:  # noqa: BLE001
            any_failure = True
            LOG.error("country=%s extract failed: %s — keeping last good", plan.slug, exc)
            continue

        if not records:
            LOG.info("country=%s zero repeaters — skip quietly", plan.slug)
            continue

        digest = content_sha256(records, networks)
        live = _live_content_sha256(cfg.publish_root, plan.slug)
        if live and live == digest:
            LOG.info("country=%s unchanged content_sha256=%s — skip new generation", plan.slug, digest)
            continue

        env_doc = envelope(
            country=plan.country_name,
            country_slug=plan.slug,
            generation=gen,
            fetched_unix=fetched_unix,
            records=records,
            networks=networks,
            stats=stats,
        )
        extra = {
            "content_sha256": digest,
            "stats": stats,
            "inputs": stats.get("inputs"),
        }
        try:
            stage_dir, manifest = stage_and_validate(
                cfg.publish_root,
                source=SOURCE_ID,
                country=plan.country_name,
                country_slug=plan.slug,
                generation=gen,
                fetched_unix=fetched_unix,
                envelope=env_doc,
                stage_root=stage_root,
                attribution=attr,
                licence=LICENCE_NOTE,
                ratio_min=cfg.count_ratio_min,
                ratio_max=cfg.count_ratio_max,
                extra_manifest=extra,
            )
            promote_generation(
                cfg.publish_root,
                SOURCE_ID,
                plan.slug,
                gen,
                stage_dir,
                keep_generations=cfg.keep_generations,
            )
            meta = country_meta_from_manifest(
                manifest, source=SOURCE_ID, country_slug=plan.slug, generation=gen
            )
            meta["content_sha256"] = digest
            countries_meta.append(meta)
            published_any = True
            LOG.info(
                "country=%s published generation=%s records=%s networks=%s",
                plan.slug,
                gen,
                len(records),
                len(networks),
            )
        except PublishError as exc:
            any_failure = True
            LOG.error("country=%s publish refused: %s", plan.slug, exc)

    if not published_any:
        if any_failure:
            LOG.error("no osm countries published; current.json unchanged")
            return 1
        LOG.info("no osm countries needed publish (all empty or unchanged)")
        return 0

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
    p = argparse.ArgumentParser(description="Navi OSM amateur-radio repeaters extract")
    p.add_argument("--dry-run-fixtures", metavar="DIR")
    p.add_argument("--publish-root", metavar="DIR")
    args = p.parse_args(argv)
    env = dict(os.environ)
    if args.publish_root and not args.dry_run_fixtures:
        env.setdefault("NAVI_OSM_REPEATERS_ENABLED", "1")
        cfg = load_config(env)
        pub = Path(args.publish_root)
        pub.mkdir(parents=True, exist_ok=True)
        cfg = replace(cfg, publish_root=pub, work_dir=pub.parent / "osm-repeaters-work")
        return run_extract(cfg, environ=env)
    if args.dry_run_fixtures:
        env["NAVI_OSM_REPEATERS_DRY_RUN"] = "1"
        env.setdefault("NAVI_OSM_REPEATERS_ENABLED", "1")
        fix = Path(args.dry_run_fixtures)
        cfg = load_config(env)
        # Expect norway.synthetic.opl + sweden.synthetic.opl (+ optional .net.opl)
        # and regions.conf under fixtures; test script may pre-split rep/net.
        fixture_by: Dict[str, Dict[str, Tuple[Path, Path]]] = {}
        for slug, stem in (("no", "norway"), ("se", "sweden")):
            rep = fix / f"{stem}.rep.opl"
            net = fix / f"{stem}.net.opl"
            if not rep.is_file():
                # single combined file → use same for both filters offline
                combined = fix / f"{stem}.synthetic.opl"
                if combined.is_file():
                    rep = combined
                    net = combined
                else:
                    LOG.error("missing fixture for %s under %s", slug, fix)
                    return 2
            if not net.is_file():
                net = rep
            fixture_by[slug] = {f"{stem}-a": (rep, net), f"{stem}-b": (rep, net)}
        # Prefer explicit multi-input fixtures if present
        no_a = fix / "norway_a.rep.opl"
        no_b = fix / "norway_b.rep.opl"
        if no_a.is_file() and no_b.is_file():
            net = fix / "norway.net.opl"
            if not net.is_file():
                net = fix / "norway.synthetic.opl"
            fixture_by["no"] = {
                "norway_ostlandet": (no_a, net if net.is_file() else no_a),
                "norway_vestlandet": (no_b, net if net.is_file() else no_b),
            }
        se_rep = fix / "sweden.rep.opl"
        if se_rep.is_file():
            se_net = fix / "sweden.net.opl"
            fixture_by["se"] = {
                "sweden": (se_rep, se_net if se_net.is_file() else se_rep),
            }
        env["NAVI_OSM_REPEATERS_COUNTRIES"] = "no=europe/norway,se=europe/sweden"
        cfg = load_config(env)
        if args.publish_root:
            pub = Path(args.publish_root)
            pub.mkdir(parents=True, exist_ok=True)
            cfg = replace(cfg, publish_root=pub, work_dir=pub.parent / "osm-repeaters-work")
        # regions conf from fixtures if present
        regions = fix / "regions.conf"
        if regions.is_file():
            cfg = replace(cfg, regions_conf=regions, regions_planet_conf=regions)
        return run_extract(cfg, environ=env, fixture_by_slug=fixture_by)
    return run_extract(environ=env)


if __name__ == "__main__":
    sys.exit(main())
