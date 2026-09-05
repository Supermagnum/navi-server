"""Published packs tree helpers: Geofabrik-path layout + current.json rebuild.

Publish layout mirrors Geofabrik download paths used by the Navi app's
Download-scope picker (e.g. asia/china/anhui), not the flat bake ids
(asia_china_anhui). See docs/client-fetch.md.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

# Generation directory names start with UTC stamp (optionally with -pid-suffix).
_GEN_DIR_RE = re.compile(r"^\d{8}T\d{6}")


def load_region_sources(*conf_paths: Path) -> dict[str, str]:
    """bake_id -> source string from one or more regions.conf files."""
    out: dict[str, str] = {}
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
            # First conf wins for a given id (callers put preferred conf first).
            out.setdefault(parts[0], parts[1])
    return out


def publish_relpath(bake_id: str, sources: dict[str, str]) -> str:
    """HTTP-relative path under packs/ for a bake region id.

    Geofabrik sources use the path after geofabrik: (slash-separated, hyphens
    preserved — same strings as Navi GeofabrikDownloadCatalog / Geofabrik URLs).
    Non-geofabrik sources fall back to the bake id (single segment).
    """
    src = sources.get(bake_id, "")
    if src.startswith("geofabrik:"):
        path = src.split(":", 1)[1].strip().strip("/")
        if path:
            return path
    return bake_id


def is_generation_dir(name: str) -> bool:
    return bool(_GEN_DIR_RE.match(name))


def complete_gens(region_dir: Path) -> list[Path]:
    out: list[Path] = []
    if not region_dir.is_dir():
        return out
    for d in region_dir.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        if not is_generation_dir(d.name):
            continue
        if (d / ".publish_in_progress").exists():
            continue
        if not (d / "manifest.json").exists():
            continue
        out.append(d)
    out.sort(key=lambda p: p.name, reverse=True)
    return out


def iter_published_region_dirs(packs_root: Path) -> list[Path]:
    """Leaf region directories: any dir under packs/ that has generation children."""
    if not packs_root.is_dir():
        return []
    found: list[Path] = []
    for dirpath, dirnames, _filenames in os.walk(packs_root):
        # Do not descend into generation dirs.
        gens = [n for n in dirnames if is_generation_dir(n)]
        if gens:
            found.append(Path(dirpath))
            dirnames[:] = [n for n in dirnames if not is_generation_dir(n)]
    return sorted(found, key=lambda p: p.as_posix())


def rebuild_current_json(
    published: Path,
    *,
    generation: str,
    extra: dict | None = None,
) -> list[dict]:
    """Rewrite current.json from the packs tree (nested or legacy-flat)."""
    packs_root = published / "packs"
    regions: list[dict] = []
    for region_dir in iter_published_region_dirs(packs_root):
        gens = complete_gens(region_dir)
        if not gens:
            continue
        g = gens[0]
        rel = region_dir.relative_to(packs_root).as_posix()
        try:
            gman = json.loads((g / "manifest.json").read_text(encoding="utf-8"))
            g_dh = bool(gman.get("has_delta_h"))
            bake_id = gman.get("bake_id")
        except Exception:
            g_dh = None
            bake_id = None
        entry = {
            "region_id": rel,
            "generation": g.name,
            "manifest_url": f"/packs/{rel}/{g.name}/manifest.json",
            "has_delta_h": g_dh,
            "bytes": sum(f.stat().st_size for f in g.rglob("*") if f.is_file()),
        }
        if bake_id:
            entry["bake_id"] = bake_id
        regions.append(entry)

    current = {
        "schema": 1,
        "generation": generation,
        "created_unix": int(time.time()),
        "packs_base": "/packs",
        "layout": "geofabrik-path",
        "regions": regions,
    }
    if extra:
        current.update(extra)
    tmp = published / "current.json.partial"
    tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, published / "current.json")
    return regions
