"""Geometry-hash result cache for elevation samples and wetland/boardwalk.

Key: blake2b-128 over the quantised (1e-5°) lat/lon sequence plus CoMaps
altitudes in decimetres (so a CoMaps DEM change without node moves still
misses). Values store ABSOLUTE Mapterhorn altitudes per point (int16 dm),
plus Soft/Hard wetland segment records and boardwalk flags. Threshold /
policy changes reapply without refetching tiles.

Policy epoch (stored in DB meta + per-row): quantum, Mapterhorn dataset
version, zoom, wetland Soft/Hard type sets, boardwalk rule, dump schema.
Threshold is intentionally NOT part of the epoch.

Storage: one SQLite DB (WAL) at NAVI_MWM_GEOMETRY_CACHE. World size estimate
~6–14 GiB depending on packing (see docs / next-release report).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

QUANTUM_DEG = 1e-5
DUMP_SCHEMA = "dump_mwm_roads/v1"
WETLAND_POLICY = "soft=bog,string_bog,fen,untyped;hard=swamp,reedbed;omit=marsh"
BOARDWALK_RULE = "bridge_types_proxy/v1"
MAPTERHORN_DATASET = "glo30+national/terrarium-z"
DEFAULT_ZOOM = 12

# Expected world footprint (from runtime §6.3)
WORLD_SIZE_ESTIMATE = {
    "packed_zstd_gib": 4.56,
    "packed_raw_gib": 6.85,
    "sqlite_btree_gib": 13.7,
}


def policy_epoch(
    *,
    zoom: int = DEFAULT_ZOOM,
    quantum: float = QUANTUM_DEG,
    mapterhorn_dataset: str = MAPTERHORN_DATASET,
    wetland_policy: str = WETLAND_POLICY,
    boardwalk_rule: str = BOARDWALK_RULE,
    dump_schema: str = DUMP_SCHEMA,
) -> str:
    """Stable epoch id. Threshold omitted on purpose (absolute samples)."""
    q = f"{quantum:.0e}" if quantum != QUANTUM_DEG else "1e-5"
    return (
        f"q={q}|mh={mapterhorn_dataset}|z={int(zoom)}|"
        f"wet={wetland_policy}|bw={boardwalk_rule}|dump={dump_schema}"
    )


def quantise_coord(value: float, quantum: float = QUANTUM_DEG) -> int:
    return int(round(float(value) / quantum))


def road_geometry_hash(
    points: Iterable[dict],
    *,
    quantum: float = QUANTUM_DEG,
) -> bytes:
    """blake2b-128 of quantised (lat,lon) + alt_dm sequence."""
    h = hashlib.blake2b(digest_size=16)
    for p in points:
        qlat = quantise_coord(float(p["lat"]), quantum)
        qlon = quantise_coord(float(p["lon"]), quantum)
        alt_dm = int(round(float(p.get("alt_m", 0.0)) * 10.0))
        if alt_dm < -32768:
            alt_dm = -32768
        elif alt_dm > 32767:
            alt_dm = 32767
        h.update(struct.pack("<iih", qlat, qlon, alt_dm))
    return h.digest()


def wetland_polygon_hash(triangles_or_rings: Iterable[Any], *, quantum: float = QUANTUM_DEG) -> bytes:
    """blake2b-128 over quantised wetland triangle/ring vertices."""
    h = hashlib.blake2b(digest_size=16)
    for item in triangles_or_rings:
        coords: list[tuple[float, float]]
        if isinstance(item, dict):
            if "points" in item:
                coords = [(float(p["lat"]), float(p["lon"])) for p in item["points"]]
            elif "lat" in item and "lon" in item:
                coords = [(float(item["lat"]), float(item["lon"]))]
            else:
                continue
        elif isinstance(item, (list, tuple)) and len(item) >= 2 and not isinstance(item[0], (list, tuple, dict)):
            coords = [(float(item[0]), float(item[1]))]
        else:
            coords = []
            for p in item:
                if isinstance(p, dict):
                    coords.append((float(p["lat"]), float(p["lon"])))
                else:
                    coords.append((float(p[0]), float(p[1])))
        for lat, lon in coords:
            h.update(struct.pack("<ii", quantise_coord(lat, quantum), quantise_coord(lon, quantum)))
    return h.digest()


def pack_samples_dm(samples: list[int | None]) -> bytes:
    """Pack absolute MH altitudes: int16 dm, -32768 = missing sample."""
    out = bytearray(len(samples) * 2)
    for i, v in enumerate(samples):
        if v is None:
            dm = -32768
        else:
            dm = int(v)
            if dm < -32767:
                dm = -32767
            elif dm > 32767:
                dm = 32767
        struct.pack_into("<h", out, i * 2, dm)
    return bytes(out)


def unpack_samples_dm(blob: bytes) -> list[int | None]:
    n = len(blob) // 2
    out: list[int | None] = []
    for i in range(n):
        (dm,) = struct.unpack_from("<h", blob, i * 2)
        out.append(None if dm == -32768 else dm)
    return out


def overrides_from_samples(
    samples_dm: list[int | None],
    comaps_alt_m: list[float],
    threshold_m: float,
) -> list[dict]:
    """Reapply threshold to absolute MH samples vs CoMaps altitudes."""
    overrides = []
    for idx, dm in enumerate(samples_dm):
        if dm is None:
            continue
        mh = dm / 10.0
        comaps = float(comaps_alt_m[idx]) if idx < len(comaps_alt_m) else 0.0
        if abs(mh - comaps) <= threshold_m:
            continue
        overrides.append({"point_index": idx, "altitude_dm": int(dm)})
    return overrides


def pack_wetland_records(records: list[dict]) -> bytes:
    """segment_index u16, direction u8, class u8 per record."""
    out = bytearray()
    for r in records:
        out += struct.pack(
            "<HBB",
            int(r["segment_index"]) & 0xFFFF,
            int(r["direction"]) & 0xFF,
            int(r["class"]) & 0xFF,
        )
    return bytes(out)


def unpack_wetland_records(blob: bytes) -> list[dict]:
    out = []
    for i in range(0, len(blob), 4):
        if i + 4 > len(blob):
            break
        seg, direction, cls = struct.unpack_from("<HBB", blob, i)
        out.append({"segment_index": seg, "direction": direction, "class": cls})
    return out


def pack_boardwalk_records(records: list[dict]) -> bytes:
    out = bytearray()
    for r in records:
        out += struct.pack(
            "<HBB",
            int(r["segment_index"]) & 0xFFFF,
            int(r["direction"]) & 0xFF,
            int(r.get("flag", 1)) & 0xFF,
        )
    return bytes(out)


def unpack_boardwalk_records(blob: bytes) -> list[dict]:
    out = []
    for i in range(0, len(blob), 4):
        if i + 4 > len(blob):
            break
        seg, direction, flag = struct.unpack_from("<HBB", blob, i)
        out.append({"segment_index": seg, "direction": direction, "flag": flag})
    return out


@dataclass
class RoadCacheEntry:
    geom_hash: bytes
    n_points: int
    samples_dm: list[int | None]
    wetland: list[dict] = field(default_factory=list)
    boardwalk: list[dict] = field(default_factory=list)
    wetland_touch_hashes: bytes = b""  # concatenated 16-byte poly hashes


class GeometryResultCache:
    """Thread-safe SQLite geometry-hash store (WAL)."""

    def __init__(self, path: Path, *, epoch: str | None = None, zoom: int = DEFAULT_ZOOM):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.epoch = epoch or policy_epoch(zoom=zoom)
        self._local = threading.local()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        conn = sqlite3.connect(str(self.path), timeout=60.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=60000")
        self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS roads (
              geom_hash BLOB NOT NULL,
              epoch TEXT NOT NULL,
              n_points INTEGER NOT NULL,
              samples_dm BLOB NOT NULL,
              wetland_blob BLOB NOT NULL,
              boardwalk_blob BLOB NOT NULL,
              wetland_touch BLOB NOT NULL,
              PRIMARY KEY (geom_hash, epoch)
            );
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('policy_epoch', ?)",
            (self.epoch,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema', ?)",
            ("navi-mwm-geometry-cache/v1",),
        )
        conn.commit()

    def get_road(self, geom_hash: bytes, *, epoch: str | None = None) -> RoadCacheEntry | None:
        ep = epoch or self.epoch
        conn = self._connect()
        row = conn.execute(
            "SELECT n_points, samples_dm, wetland_blob, boardwalk_blob, wetland_touch "
            "FROM roads WHERE geom_hash=? AND epoch=?",
            (geom_hash, ep),
        ).fetchone()
        if row is None:
            return None
        n_points, samples_dm, wet, bw, touch = row
        return RoadCacheEntry(
            geom_hash=geom_hash,
            n_points=int(n_points),
            samples_dm=unpack_samples_dm(samples_dm),
            wetland=unpack_wetland_records(wet or b""),
            boardwalk=unpack_boardwalk_records(bw or b""),
            wetland_touch_hashes=touch or b"",
        )

    def put_road(self, entry: RoadCacheEntry, *, epoch: str | None = None) -> None:
        ep = epoch or self.epoch
        conn = self._connect()
        conn.execute(
            "INSERT OR REPLACE INTO roads("
            "geom_hash, epoch, n_points, samples_dm, wetland_blob, boardwalk_blob, wetland_touch"
            ") VALUES (?,?,?,?,?,?,?)",
            (
                entry.geom_hash,
                ep,
                int(entry.n_points),
                pack_samples_dm(entry.samples_dm),
                pack_wetland_records(entry.wetland),
                pack_boardwalk_records(entry.boardwalk),
                entry.wetland_touch_hashes or b"",
            ),
        )
        conn.commit()

    def put_roads_many(self, entries: Iterable[RoadCacheEntry], *, epoch: str | None = None) -> int:
        ep = epoch or self.epoch
        conn = self._connect()
        rows = [
            (
                e.geom_hash,
                ep,
                int(e.n_points),
                pack_samples_dm(e.samples_dm),
                pack_wetland_records(e.wetland),
                pack_boardwalk_records(e.boardwalk),
                e.wetland_touch_hashes or b"",
            )
            for e in entries
        ]
        if not rows:
            return 0
        conn.executemany(
            "INSERT OR REPLACE INTO roads("
            "geom_hash, epoch, n_points, samples_dm, wetland_blob, boardwalk_blob, wetland_touch"
            ") VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        return len(rows)

    def stats(self) -> dict[str, Any]:
        conn = self._connect()
        n = conn.execute("SELECT COUNT(*) FROM roads").fetchone()[0]
        size = self.path.stat().st_size if self.path.is_file() else 0
        return {
            "path": str(self.path),
            "epoch": self.epoch,
            "road_rows": int(n),
            "db_bytes": int(size),
            "world_size_estimate_gib": WORLD_SIZE_ESTIMATE,
        }

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def default_cache_path() -> Path:
    raw = os.environ.get("NAVI_MWM_GEOMETRY_CACHE", "").strip()
    if raw:
        return Path(raw)
    # Scratch default next to Mapterhorn tile cache parent.
    mh = os.environ.get("NAVI_MWM_MAPTERHORN_CACHE", "").strip()
    if mh:
        return Path(mh).resolve().parent / "geometry-hash-cache.sqlite"
    return Path("/tmp/navi-mwm-geometry-hash-cache.sqlite")


def open_geometry_cache(*, zoom: int | None = None) -> GeometryResultCache | None:
    """Open cache when NAVI_MWM_GEOMETRY_CACHE is set (or explicit enable=1)."""
    enabled = os.environ.get("NAVI_MWM_GEOMETRY_CACHE_ENABLE", "").strip()
    path_raw = os.environ.get("NAVI_MWM_GEOMETRY_CACHE", "").strip()
    if enabled in ("0", "false", "no", "off"):
        return None
    if not path_raw and enabled not in ("1", "true", "yes", "on"):
        return None
    z = zoom if zoom is not None else int(os.environ.get("NAVI_MWM_MAPTERHORN_ZOOM", str(DEFAULT_ZOOM)) or DEFAULT_ZOOM)
    return GeometryResultCache(default_cache_path(), zoom=z)


def iter_roads_jsonl(path: Path) -> Iterator[dict]:
    with path.open() as f:
        for line in f:
            if line.strip():
                yield json.loads(line)
