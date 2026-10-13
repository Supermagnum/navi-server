"""Map bake regions → country prefixes and ISO slugs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# Geofabrik / OSM.fr paths that span multiple countries — skip unless overridden.
MULTI_COUNTRY_PREFIXES = frozenset(
    {
        "europe/dach",
        "europe/alps",
        "europe/britain-and-ireland",
        "europe/gcc-states",
        "africa",
        "antarctica",
        "asia",
        "australia-oceania",
        "central-america",
        "europe",
        "north-america",
        "south-america",
    }
)

# continent/country → ISO 3166-1 alpha-2 (lowercase), matching RB/RadioID slugs.
PATH_TO_SLUG: Dict[str, str] = {
    "africa/algeria": "dz",
    "africa/egypt": "eg",
    "africa/ethiopia": "et",
    "africa/kenya": "ke",
    "africa/madagascar": "mg",
    "africa/morocco": "ma",
    "africa/nigeria": "ng",
    "africa/south-africa": "za",
    "africa/tanzania": "tz",
    "asia/afghanistan": "af",
    "asia/bangladesh": "bd",
    "asia/cambodia": "kh",
    "asia/china": "cn",
    "asia/gcc-states": "gcc",
    "asia/india": "in",
    "asia/indonesia": "id",
    "asia/iran": "ir",
    "asia/iraq": "iq",
    "asia/israel-and-palestine": "il",
    "asia/japan": "jp",
    "asia/jordan": "jo",
    "asia/kazakhstan": "kz",
    "asia/korea": "kr",
    "asia/lebanon": "lb",
    "asia/malaysia-singapore-brunei": "my",
    "asia/mongolia": "mn",
    "asia/myanmar": "mm",
    "asia/nepal": "np",
    "asia/north-korea": "kp",
    "asia/pakistan": "pk",
    "asia/philippines": "ph",
    "asia/saudi-arabia": "sa",
    "asia/south-korea": "kr",
    "asia/sri-lanka": "lk",
    "asia/syria": "sy",
    "asia/taiwan": "tw",
    "asia/thailand": "th",
    "asia/turkey": "tr",
    "asia/turkiye": "tr",
    "asia/united-arab-emirates": "ae",
    "asia/vietnam": "vn",
    "asia/yemen": "ye",
    "australia-oceania/australia": "au",
    "australia-oceania/fiji": "fj",
    "australia-oceania/new-caledonia": "nc",
    "australia-oceania/new-zealand": "nz",
    "australia-oceania/papua-new-guinea": "pg",
    "central-america/cuba": "cu",
    "central-america/guatemala": "gt",
    "central-america/haiti-and-domrep": "ht",
    "central-america/nicaragua": "ni",
    "europe/albania": "al",
    "europe/andorra": "ad",
    "europe/austria": "at",
    "europe/azores": "pt",
    "europe/belarus": "by",
    "europe/belgium": "be",
    "europe/bosnia-herzegovina": "ba",
    "europe/bulgaria": "bg",
    "europe/croatia": "hr",
    "europe/cyprus": "cy",
    "europe/czech-republic": "cz",
    "europe/denmark": "dk",
    "europe/estonia": "ee",
    "europe/faroe-islands": "fo",
    "europe/finland": "fi",
    "europe/france": "fr",
    "europe/georgia": "ge",
    "europe/germany": "de",
    "europe/great-britain": "gb",
    "europe/greece": "gr",
    "europe/hungary": "hu",
    "europe/iceland": "is",
    "europe/ireland-and-northern-ireland": "ie",
    "europe/isle-of-man": "im",
    "europe/italy": "it",
    "europe/kosovo": "xk",
    "europe/latvia": "lv",
    "europe/liechtenstein": "li",
    "europe/lithuania": "lt",
    "europe/luxembourg": "lu",
    "europe/macedonia": "mk",
    "europe/malta": "mt",
    "europe/moldova": "md",
    "europe/monaco": "mc",
    "europe/montenegro": "me",
    "europe/netherlands": "nl",
    "europe/norway": "no",
    "europe/poland": "pl",
    "europe/portugal": "pt",
    "europe/romania": "ro",
    "europe/russia": "ru",
    "europe/serbia": "rs",
    "europe/slovakia": "sk",
    "europe/slovenia": "si",
    "europe/spain": "es",
    "europe/sweden": "se",
    "europe/switzerland": "ch",
    "europe/turkey": "tr",
    "europe/turkiye": "tr",
    "europe/ukraine": "ua",
    "europe/united-kingdom": "gb",
    "north-america/canada": "ca",
    "north-america/greenland": "gl",
    "north-america/mexico": "mx",
    "north-america/us": "us",
    "north-america/usa": "us",
    "south-america/argentina": "ar",
    "south-america/bolivia": "bo",
    "south-america/brazil": "br",
    "south-america/chile": "cl",
    "south-america/colombia": "co",
    "south-america/ecuador": "ec",
    "south-america/paraguay": "py",
    "south-america/peru": "pe",
    "south-america/uruguay": "uy",
    "south-america/venezuela": "ve",
}


@dataclass(frozen=True)
class RegionInput:
    region_id: str
    source: str
    publish_relpath: str
    pbf_path: Path


@dataclass(frozen=True)
class CountryPlan:
    slug: str
    prefix: str
    country_name: str
    regions: Tuple[RegionInput, ...]


def publish_relpath_for_source(bake_id: str, source: str) -> str:
    """Match scripts/lib/common.sh region_publish_relpath."""
    src = (source or "").strip()
    if src.startswith("geofabrik:"):
        path = src.split(":", 1)[1].strip().strip("/")
        if path:
            return path
    osmfr = "url:https://download.openstreetmap.fr/extracts/"
    if src.startswith(osmfr):
        rest = src[len(osmfr) :]
        if rest.endswith("-latest.osm.pbf"):
            rest = rest[: -len("-latest.osm.pbf")]
        elif rest.endswith(".osm.pbf"):
            rest = rest[: -len(".osm.pbf")]
        rest = rest.strip("/")
        if rest:
            return rest
    return bake_id


def country_prefix_from_relpath(relpath: str) -> Optional[str]:
    parts = [p for p in relpath.strip("/").split("/") if p]
    if len(parts) < 2:
        return None
    prefix = f"{parts[0]}/{parts[1]}"
    if prefix in MULTI_COUNTRY_PREFIXES or parts[0] in MULTI_COUNTRY_PREFIXES:
        # continent-only or known multi-country leaf
        if prefix in MULTI_COUNTRY_PREFIXES:
            return None
    return prefix


def slug_for_prefix(prefix: str) -> str:
    if prefix in PATH_TO_SLUG:
        return PATH_TO_SLUG[prefix]
    # fallback: last path segment, lowercased, hyphen→underscore stripped to alnum
    last = prefix.rsplit("/", 1)[-1].lower()
    return last.replace("_", "-")


def country_display_name(prefix: str) -> str:
    last = prefix.rsplit("/", 1)[-1]
    return last.replace("-", " ").title()


def load_region_lines(*conf_paths: Path) -> List[Tuple[str, str]]:
    """Return [(region_id, source), ...] first-wins across conf files."""
    seen: set[str] = set()
    out: List[Tuple[str, str]] = []
    for conf in conf_paths:
        if not conf or not conf.is_file():
            continue
        for raw in conf.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            rid, src = parts[0], parts[1]
            if rid in seen:
                continue
            seen.add(rid)
            out.append((rid, src))
    return out


def parse_countries_override(raw: Optional[str]) -> Optional[Dict[str, str]]:
    """Parse ``no=europe/norway,se=europe/sweden`` → slug→prefix. Empty → None (all)."""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    out: Dict[str, str] = {}
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"NAVI_OSM_REPEATERS_COUNTRIES entry needs slug=prefix: {part!r}")
        slug, prefix = part.split("=", 1)
        slug = slug.strip().lower()
        prefix = prefix.strip().strip("/")
        if not slug or not prefix:
            raise ValueError(f"bad NAVI_OSM_REPEATERS_COUNTRIES entry: {part!r}")
        out[slug] = prefix
    return out or None


def plan_countries(
    *,
    conf_paths: Sequence[Path],
    extracts_dir: Path,
    countries_override: Optional[Mapping[str, str]] = None,
) -> List[CountryPlan]:
    rows = load_region_lines(*conf_paths)
    by_prefix: Dict[str, List[RegionInput]] = {}
    for rid, src in rows:
        rel = publish_relpath_for_source(rid, src)
        prefix = country_prefix_from_relpath(rel)
        if prefix is None:
            continue
        pbf = extracts_dir / f"{rid}-latest.osm.pbf"
        by_prefix.setdefault(prefix, []).append(
            RegionInput(region_id=rid, source=src, publish_relpath=rel, pbf_path=pbf)
        )

    plans: List[CountryPlan] = []
    if countries_override:
        for slug, prefix in countries_override.items():
            prefix = prefix.strip("/")
            regions = [
                r
                for r in _all_regions(by_prefix)
                if r.publish_relpath == prefix or r.publish_relpath.startswith(prefix + "/")
            ]
            # Also accept exact prefix key if grouped differently
            if not regions and prefix in by_prefix:
                regions = list(by_prefix[prefix])
            plans.append(
                CountryPlan(
                    slug=slug,
                    prefix=prefix,
                    country_name=country_display_name(prefix),
                    regions=tuple(regions),
                )
            )
        return plans

    for prefix in sorted(by_prefix.keys()):
        slug = slug_for_prefix(prefix)
        plans.append(
            CountryPlan(
                slug=slug,
                prefix=prefix,
                country_name=country_display_name(prefix),
                regions=tuple(by_prefix[prefix]),
            )
        )
    return plans


def _all_regions(by_prefix: Mapping[str, List[RegionInput]]) -> Iterable[RegionInput]:
    for regs in by_prefix.values():
        yield from regs
