"""Config for the OSM repeaters extract. No network. No secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from plugins.datex_common.enable import is_truthy

from .countries import parse_countries_override

DEFAULT_KEEP_GENERATIONS = 2


@dataclass(frozen=True)
class Config:
    enabled: bool
    pack_root: Path
    extracts_dir: Path
    regions_conf: Path
    regions_planet_conf: Path
    work_dir: Path
    publish_root: Path
    keep_generations: int
    count_ratio_min: float
    count_ratio_max: float
    countries_override: Optional[dict[str, str]]


def is_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return is_truthy(env.get("NAVI_OSM_REPEATERS_ENABLED"), default=False)


def load_config(environ: Mapping[str, str] | None = None) -> Config:
    env = environ if environ is not None else os.environ
    pack_root = Path(env.get("NAVI_PACK_ROOT", "/media/navi/navi-server/data")).expanduser()
    extracts = Path(
        env.get("NAVI_EXTRACTS_DIR", str(pack_root / "scratch" / "extracts"))
    ).expanduser()
    regions = Path(env.get("NAVI_REGIONS_CONF", str(pack_root / "regions.conf"))).expanduser()
    planet = Path(
        env.get("NAVI_REGIONS_PLANET_CONF", str(pack_root / "regions.planet.conf"))
    ).expanduser()
    keep = int(env.get("NAVI_OSM_REPEATERS_KEEP_GENERATIONS", str(DEFAULT_KEEP_GENERATIONS)))
    if keep < 1:
        keep = 1
    override = parse_countries_override(env.get("NAVI_OSM_REPEATERS_COUNTRIES"))
    return Config(
        enabled=is_enabled(env),
        pack_root=pack_root,
        extracts_dir=extracts,
        regions_conf=regions,
        regions_planet_conf=planet,
        work_dir=pack_root / "osm_repeaters",
        publish_root=pack_root / "published" / "repeaters",
        keep_generations=keep,
        count_ratio_min=float(env.get("NAVI_OSM_REPEATERS_COUNT_RATIO_MIN", "0.25")),
        count_ratio_max=float(env.get("NAVI_OSM_REPEATERS_COUNT_RATIO_MAX", "4.0")),
        countries_override=override,
    )
