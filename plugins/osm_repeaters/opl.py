"""Parse Osmium OPL lines (no third-party deps)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

_ESCAPE_RE = re.compile(r"%([0-9A-Fa-f]{2})%")


def unescape_opl(value: str) -> str:
    """Decode OPL ``%<hex>%`` escapes to Unicode code points."""

    def _one(m: re.Match[str]) -> str:
        return chr(int(m.group(1), 16))

    return _ESCAPE_RE.sub(_one, value)


@dataclass
class OplElement:
    osm_type: str  # node | way | relation
    osm_id: int
    version: int = 0
    timestamp: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    tags: Dict[str, str] = field(default_factory=dict)
    nodes: List[int] = field(default_factory=list)  # way node refs
    members: List[Tuple[str, int, str]] = field(default_factory=list)  # type, id, role


def _parse_member_token(tok: str) -> Optional[Tuple[str, int, str]]:
    # n123@role / Mn123@role / w456@ / r789@outer
    if not tok:
        return None
    if tok[0] == "M" and len(tok) > 1:
        tok = tok[1:]
    if len(tok) < 2:
        return None
    kind = tok[0]
    rest = tok[1:]
    type_map = {"n": "node", "w": "way", "r": "relation"}
    if kind not in type_map:
        return None
    if "@" in rest:
        id_s, role = rest.split("@", 1)
    else:
        id_s, role = rest, ""
    try:
        oid = int(id_s)
    except ValueError:
        return None
    return type_map[kind], oid, unescape_opl(role)


def parse_opl_line(line: str) -> Optional[OplElement]:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    parts = line.split()
    head = parts[0]
    if not head or head[0] not in "nwr":
        return None
    type_map = {"n": "node", "w": "way", "r": "relation"}
    try:
        osm_id = int(head[1:])
    except ValueError:
        return None
    el = OplElement(osm_type=type_map[head[0]], osm_id=osm_id)
    for tok in parts[1:]:
        if tok.startswith("v") and tok[1:].isdigit():
            el.version = int(tok[1:])
        elif tok.startswith("t") and len(tok) > 1 and tok[1].isdigit():
            # t2026-09-01T12:34:56Z
            el.timestamp = tok[1:]
        elif tok.startswith("timestamp="):
            el.timestamp = unescape_opl(tok.split("=", 1)[1])
        elif tok.startswith("lat="):
            try:
                el.lat = float(tok.split("=", 1)[1])
            except ValueError:
                pass
        elif tok.startswith("lon="):
            try:
                el.lon = float(tok.split("=", 1)[1])
            except ValueError:
                pass
        elif tok.startswith("y") and len(tok) > 1 and (tok[1].isdigit() or tok[1] == "-"):
            # Osmium OPL: y=lat
            try:
                el.lat = float(tok[1:])
            except ValueError:
                pass
        elif tok.startswith("x") and len(tok) > 1 and (tok[1].isdigit() or tok[1] == "-"):
            # Osmium OPL: x=lon
            try:
                el.lon = float(tok[1:])
            except ValueError:
                pass
        elif tok.startswith("T") and "=" in tok:
            # Osmium: one T field with comma-separated key=value pairs.
            # Do not split on decimal commas inside values — only on ,key=
            body = tok[1:]
            for pair in re.split(r",(?=[^=,]+=)", body):
                if "=" not in pair:
                    continue
                key, val = pair.split("=", 1)
                el.tags[unescape_opl(key)] = unescape_opl(val)
        elif tok.startswith("N"):
            # Nn1,n2,n3
            body = tok[1:]
            for piece in body.split(","):
                piece = piece.strip()
                if piece.startswith("n"):
                    piece = piece[1:]
                if piece.isdigit():
                    el.nodes.append(int(piece))
        elif tok.startswith("M"):
            # Mn1@role,w2@,r3@outer
            body = tok[1:]
            for piece in body.split(","):
                m = _parse_member_token(piece)
                if m:
                    el.members.append(m)
    return el


def iter_opl_file(path) -> Iterator[OplElement]:
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            el = parse_opl_line(line)
            if el is not None:
                yield el
