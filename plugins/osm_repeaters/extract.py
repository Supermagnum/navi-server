"""Run osmium tags-filter and assemble per-country repeater + network sets."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from . import REPEATER_KEY
from .countries import CountryPlan, RegionInput
from .normalize import network_record, normalize_repeater
from .opl import OplElement, iter_opl_file

LOG = logging.getLogger("navi.osm_repeaters")

OsmiumRunner = Callable[[List[str]], subprocess.CompletedProcess]


def _default_osmium(argv: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, check=False, text=True)


def osmium_replication_timestamp(
    pbf: Path, *, runner: Optional[OsmiumRunner] = None
) -> Optional[str]:
    run = runner or _default_osmium
    proc = run(
        [
            "osmium",
            "fileinfo",
            "-g",
            "header.option.osmosis_replication_timestamp",
            str(pbf),
        ]
    )
    if proc.returncode != 0:
        return None
    val = (proc.stdout or "").strip()
    return val or None


def tags_filter_to_opl(
    pbf: Path,
    expression: str,
    out_opl: Path,
    *,
    omit_referenced: bool = False,
    runner: Optional[OsmiumRunner] = None,
) -> None:
    run = runner or _default_osmium
    out_opl.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "osmium",
        "tags-filter",
        str(pbf),
        expression,
        "-f",
        "opl",
        "-o",
        str(out_opl),
        "--overwrite",
    ]
    if omit_referenced:
        cmd.insert(2, "-R")  # after tags-filter → actually after subcommand name
        # Fix: -R belongs after tags-filter
        cmd = [
            "osmium",
            "tags-filter",
            "-R",
            str(pbf),
            expression,
            "-f",
            "opl",
            "-o",
            str(out_opl),
            "--overwrite",
        ]
    proc = run(cmd)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "")[:400]
        raise RuntimeError(f"osmium tags-filter failed rc={proc.returncode}: {err}")


def _way_centroid(
    way: OplElement, nodes_by_id: Mapping[int, OplElement]
) -> Tuple[Optional[float], Optional[float]]:
    lats: List[float] = []
    lons: List[float] = []
    seen: Set[int] = set()
    for nid in way.nodes:
        if nid in seen:
            continue
        seen.add(nid)
        n = nodes_by_id.get(nid)
        if n and n.lat is not None and n.lon is not None:
            lats.append(n.lat)
            lons.append(n.lon)
    if not lats:
        return None, None
    return sum(lats) / len(lats), sum(lons) / len(lons)


def extract_from_opl_pair(
    rep_opl: Path,
    net_opl: Path,
    *,
    stats: Optional[Dict[str, int]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    """Parse repeater + network OPL files into records (single PBF extract)."""
    st: Dict[str, int] = dict(stats or {})
    nodes_by_id: Dict[int, OplElement] = {}
    repeaters_raw: List[OplElement] = []
    for el in iter_opl_file(rep_opl):
        if el.osm_type == "node":
            nodes_by_id[el.osm_id] = el
        if REPEATER_KEY in el.tags:
            repeaters_raw.append(el)

    # Index networks
    networks_raw = [el for el in iter_opl_file(net_opl) if el.osm_type == "relation"]

    # First pass: build preliminary records without network membership
    prelim: List[Tuple[OplElement, Dict[str, Any]]] = []
    for el in repeaters_raw:
        if el.osm_type == "node":
            lat, lon = el.lat, el.lon
            coord_source = "osm_node" if lat is not None and lon is not None else None
        elif el.osm_type == "way":
            lat, lon = _way_centroid(el, nodes_by_id)
            coord_source = "osm_way_centroid" if lat is not None else None
            st["ways"] = st.get("ways", 0) + 1
        else:
            # relation repeater — skipped inside normalize
            lat = lon = None
            coord_source = None
        if el.osm_type == "node":
            st["nodes"] = st.get("nodes", 0) + 1
        rec = normalize_repeater(
            el, lat=lat, lon=lon, coord_source=coord_source, stats=st
        )
        if rec is None:
            continue
        if rec.get("entry_kind") == "aprs":
            st["aprs"] = st.get("aprs", 0) + 1
        prelim.append((el, rec))

    repeater_keys = {(r["osm_type"], r["osm_id"]) for _, r in prelim}

    # Network membership per repeater
    rel_by_member: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    networks_out: List[Dict[str, Any]] = []
    for nel in networks_raw:
        nr = network_record(nel, repeater_keys=repeater_keys)
        if nr is None:
            continue
        networks_out.append(nr)
        for m in nr["members"]:
            if not m.get("in_dataset"):
                continue
            key = (m["type"], m["id"])
            rel_by_member.setdefault(key, []).append(
                {
                    "relation_id": nr["relation_id"],
                    "relation_version": nr["version"],
                    "relation_timestamp": nr.get("timestamp"),
                    "name": nr.get("name"),
                    "network": nr.get("network"),
                    "role": m.get("role"),
                }
            )

    records: List[Dict[str, Any]] = []
    for el, rec in prelim:
        key = (rec["osm_type"], rec["osm_id"])
        rels = rel_by_member.get(key)
        if rels:
            net = dict(rec.get("network") or {})
            net["relations"] = rels
            if el.tags.get("network") and "name" not in net:
                net["name"] = el.tags["network"]
            rec = dict(rec)
            rec["network"] = net
        records.append(rec)

    st["networks"] = len(networks_out)
    return records, networks_out, st


def dedupe_by_osm_id(
    records: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    best: Dict[Tuple[str, int], Dict[str, Any]] = {}
    dropped = 0
    for r in records:
        key = (str(r.get("osm_type")), int(r["osm_id"]))
        prev = best.get(key)
        if prev is None:
            best[key] = r
            continue
        dropped += 1
        pv = int(prev.get("osm_version") or 0)
        nv = int(r.get("osm_version") or 0)
        if nv > pv:
            best[key] = r
        elif nv == pv:
            # stable: keep lexicographically larger timestamp / prefer existing
            pt = str(prev.get("osm_timestamp") or "")
            nt = str(r.get("osm_timestamp") or "")
            if nt > pt:
                best[key] = r
    out = sorted(best.values(), key=lambda x: (x.get("osm_type") or "", int(x["osm_id"])))
    return out, dropped


def dedupe_networks(
    networks: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    best: Dict[int, Dict[str, Any]] = {}
    for n in networks:
        rid = int(n["relation_id"])
        prev = best.get(rid)
        if prev is None or int(n.get("version") or 0) > int(prev.get("version") or 0):
            best[rid] = n
    return sorted(best.values(), key=lambda x: int(x["relation_id"]))


def extract_country(
    plan: CountryPlan,
    *,
    work_dir: Path,
    runner: Optional[OsmiumRunner] = None,
    fixture_opl: Optional[Mapping[str, Tuple[Path, Path]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Union extracts across region PBFs. Raises if any selected PBF missing (unless fixtures)."""
    all_records: List[Dict[str, Any]] = []
    all_networks: List[Dict[str, Any]] = []
    inputs: List[Dict[str, Any]] = []
    stats: Dict[str, int] = {
        "nodes": 0,
        "ways": 0,
        "aprs": 0,
        "skipped_relation_repeaters": 0,
        "deduped": 0,
        "networks": 0,
    }

    if not plan.regions and fixture_opl is None:
        raise FileNotFoundError(f"no regions for country slug={plan.slug} prefix={plan.prefix}")

    regions: Sequence[RegionInput] = plan.regions
    if fixture_opl is not None:
        # Synthetic: one virtual region per fixture key
        for rid, (rep, net) in fixture_opl.items():
            recs, nets, st = extract_from_opl_pair(rep, net, stats={})
            for k, v in st.items():
                if isinstance(v, int):
                    stats[k] = stats.get(k, 0) + v
            all_records.extend(recs)
            all_networks.extend(nets)
            inputs.append(
                {
                    "region_id": rid,
                    "path": str(rep),
                    "bytes": rep.stat().st_size if rep.is_file() else 0,
                    "osmosis_replication_timestamp": None,
                }
            )
    else:
        missing = [r for r in regions if not r.pbf_path.is_file()]
        if missing:
            names = ", ".join(m.region_id for m in missing)
            raise FileNotFoundError(f"missing PBF(s) for {plan.slug}: {names}")

        scratch = work_dir / "opl" / plan.slug
        scratch.mkdir(parents=True, exist_ok=True)
        for r in regions:
            rep_opl = scratch / f"{r.region_id}.rep.opl"
            net_opl = scratch / f"{r.region_id}.net.opl"
            tags_filter_to_opl(
                r.pbf_path,
                f"nwr/{REPEATER_KEY}",
                rep_opl,
                runner=runner,
            )
            tags_filter_to_opl(
                r.pbf_path,
                "r/type=network",
                net_opl,
                omit_referenced=True,
                runner=runner,
            )
            recs, nets, st = extract_from_opl_pair(rep_opl, net_opl, stats={})
            for k, v in st.items():
                if isinstance(v, int):
                    stats[k] = stats.get(k, 0) + v
            all_records.extend(recs)
            all_networks.extend(nets)
            ts = osmium_replication_timestamp(r.pbf_path, runner=runner)
            inputs.append(
                {
                    "region_id": r.region_id,
                    "path": str(r.pbf_path),
                    "bytes": r.pbf_path.stat().st_size,
                    "osmosis_replication_timestamp": ts,
                }
            )

    records, dropped = dedupe_by_osm_id(all_records)
    networks = dedupe_networks(all_networks)
    # Re-attach network membership after country-wide dedupe
    stats["deduped"] = dropped
    stats["networks"] = len(networks)
    meta = {"inputs": inputs, **stats}
    return records, networks, meta
