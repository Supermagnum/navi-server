"""Geometry-hash result cache — lock-free per-leaf files.

Layout under ``NAVI_MWM_GEOMETRY_CACHE`` (a directory):

* ``leaves/{safe_leaf}.ghash`` — one file per leaf, written by a single worker
  via tmp + atomic rename (no cross-process write locks).
* ``index/{hh}.idx`` — optional shard files mapping geom_hash → leaf (built
  offline / after a run); lookups for reuse across releases probe the index
  then open the leaf file.

Per road feature blob (zstd-compressed after a small header):

* blake2b-128 geom hash
* n_points u32
* absolute Mapterhorn samples as int32 dm (missing = INT32_MIN);
  per-feature payload is zstd-compressed (samples are absolute; zstd
  handles runs/deltas on plateaus)
* wetland Soft/Hard records, boardwalk flags

Policy epoch (quantum, MH dataset, zoom, wetland policy, boardwalk rule,
dump schema) is stored in the leaf file header. Threshold is NOT in the epoch.
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

try:
    import zstandard as zstd

    def _compress(raw: bytes) -> bytes:
        return zstd.ZstdCompressor(level=6).compress(raw)

    def _decompress(blob: bytes) -> bytes:
        return zstd.ZstdDecompressor().decompress(blob)

except ImportError:  # pragma: no cover - CI always has zstd via pipeline venv
    import zlib

    def _compress(raw: bytes) -> bytes:
        return zlib.compress(raw, 6)

    def _decompress(blob: bytes) -> bytes:
        return zlib.decompress(blob)

QUANTUM_DEG = 1e-5
DUMP_SCHEMA = "dump_mwm_roads/v1"
WETLAND_POLICY = "soft=bog,string_bog,fen,untyped;hard=swamp,reedbed;omit=marsh"
BOARDWALK_RULE = "bridge_types_proxy/v1"
MAPTERHORN_DATASET = "glo30+national/terrarium-z"
DEFAULT_ZOOM = 12
LEAF_MAGIC = b"GH01"
MISSING_DM = -2147483648  # i32 min
WORLD_SIZE_ESTIMATE = {
    "per_leaf_files_gib": 12.0,
    "with_index_gib": 14.0,
    "note": "absolute i32 dm samples + zstd; ~3.1 GiB for 174 mixed leaves → ~12–20 GiB world (high-alt bias); replaces prior SQLite ~13.7 GiB estimate",
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
    h = hashlib.blake2b(digest_size=16)
    for p in points:
        qlat = quantise_coord(float(p["lat"]), quantum)
        qlon = quantise_coord(float(p["lon"]), quantum)
        alt_dm = int(round(float(p.get("alt_m", 0.0)) * 10.0))
        h.update(struct.pack("<iii", qlat, qlon, alt_dm))
    return h.digest()


def pack_samples_dm(samples: list[int | None]) -> bytes:
    """Pack absolute Mapterhorn samples as little-endian i32 dm."""
    out = bytearray(len(samples) * 4)
    for i, v in enumerate(samples):
        dm = MISSING_DM if v is None else int(v)
        struct.pack_into("<i", out, i * 4, dm)
    return bytes(out)


def unpack_samples_dm(blob: bytes) -> list[int | None]:
    n = len(blob) // 4
    out: list[int | None] = []
    for i in range(n):
        (dm,) = struct.unpack_from("<i", blob, i * 4)
        out.append(None if dm == MISSING_DM else dm)
    return out


def overrides_from_samples(
    samples_dm: list[int | None],
    comaps_alt_m: list[float],
    threshold_m: float,
) -> list[dict]:
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
    wetland_touch_hashes: bytes = b""


def _safe_leaf(leaf: str) -> str:
    return leaf.replace("/", "_").replace(" ", "_")


def default_cache_dir() -> Path:
    raw = os.environ.get("NAVI_MWM_GEOMETRY_CACHE", "").strip()
    if raw:
        p = Path(raw)
        # Compat: if a legacy .sqlite path is set, use sibling directory.
        if p.suffix in (".sqlite", ".db") or p.name.endswith(".sqlite"):
            return p.with_suffix("").parent / "geometry-hash-cache"
        return p
    mh = os.environ.get("NAVI_MWM_MAPTERHORN_CACHE", "").strip()
    if mh:
        return Path(mh).resolve().parent / "geometry-hash-cache"
    return Path("/tmp/navi-mwm-geometry-hash-cache")


class GeometryResultCache:
    """Per-leaf file store. Writers own one leaf file; readers are lock-free."""

    def __init__(self, path: Path | None = None, *, epoch: str | None = None, zoom: int = DEFAULT_ZOOM):
        self.root = Path(path) if path is not None else default_cache_dir()
        self.leaves_dir = self.root / "leaves"
        self.index_dir = self.root / "index"
        self.leaves_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.epoch = epoch or policy_epoch(zoom=zoom)
        self._leaf: str | None = None
        self._entries: dict[bytes, RoadCacheEntry] = {}
        self._dirty = False

    def open_leaf(self, leaf: str) -> None:
        """Load one leaf into memory (single-writer)."""
        if self._dirty and self._leaf:
            self.flush_leaf()
        self._leaf = leaf
        self._entries = {}
        self._dirty = False
        path = self.leaves_dir / f"{_safe_leaf(leaf)}.ghash"
        if not path.is_file():
            return
        data = path.read_bytes()
        if len(data) < 8 or data[:4] != LEAF_MAGIC:
            return
        epoch_len = struct.unpack_from("<I", data, 4)[0]
        epoch = data[8 : 8 + epoch_len].decode("utf-8")
        if epoch != self.epoch:
            return  # wrong policy epoch → treat as miss
        off = 8 + epoch_len
        count = struct.unpack_from("<I", data, off)[0]
        off += 4
        for _ in range(count):
            if off + 16 + 4 + 4 > len(data):
                break
            ghash = data[off : off + 16]
            off += 16
            n_points = struct.unpack_from("<I", data, off)[0]
            off += 4
            blob_len = struct.unpack_from("<I", data, off)[0]
            off += 4
            blob = data[off : off + blob_len]
            off += blob_len
            raw = _decompress(blob)
            # samples (n*4) | wet_len u32 | wet | bw_len u32 | bw | touch
            samples_bytes = n_points * 4
            samples = unpack_samples_dm(raw[:samples_bytes])
            o = samples_bytes
            wet_len = struct.unpack_from("<I", raw, o)[0]
            o += 4
            wet = unpack_wetland_records(raw[o : o + wet_len])
            o += wet_len
            bw_len = struct.unpack_from("<I", raw, o)[0]
            o += 4
            bw = unpack_boardwalk_records(raw[o : o + bw_len])
            o += bw_len
            touch = raw[o:]
            self._entries[ghash] = RoadCacheEntry(
                geom_hash=ghash,
                n_points=n_points,
                samples_dm=samples,
                wetland=wet,
                boardwalk=bw,
                wetland_touch_hashes=touch,
            )

    def get_road(self, geom_hash: bytes, *, epoch: str | None = None) -> RoadCacheEntry | None:
        if epoch is not None and epoch != self.epoch:
            return None
        return self._entries.get(geom_hash)

    def put_road(self, entry: RoadCacheEntry, *, epoch: str | None = None) -> None:
        if epoch is not None and epoch != self.epoch:
            return
        self._entries[entry.geom_hash] = entry
        self._dirty = True

    def put_roads_many(self, entries: Iterable[RoadCacheEntry], *, epoch: str | None = None) -> int:
        n = 0
        for e in entries:
            self.put_road(e, epoch=epoch)
            n += 1
        return n

    def flush_leaf(self) -> Path | None:
        if not self._leaf:
            return None
        path = self.leaves_dir / f"{_safe_leaf(self._leaf)}.ghash"
        tmp = path.with_suffix(f".ghash.tmp.{os.getpid()}")
        epoch_b = self.epoch.encode("utf-8")
        parts = [LEAF_MAGIC, struct.pack("<I", len(epoch_b)), epoch_b, struct.pack("<I", len(self._entries))]
        for ghash, e in self._entries.items():
            samples = pack_samples_dm(e.samples_dm)
            wet = pack_wetland_records(e.wetland)
            bw = pack_boardwalk_records(e.boardwalk)
            raw = (
                samples
                + struct.pack("<I", len(wet))
                + wet
                + struct.pack("<I", len(bw))
                + bw
                + (e.wetland_touch_hashes or b"")
            )
            blob = _compress(raw)
            parts.append(ghash)
            parts.append(struct.pack("<I", int(e.n_points)))
            parts.append(struct.pack("<I", len(blob)))
            parts.append(blob)
        tmp.write_bytes(b"".join(parts))
        os.replace(tmp, path)
        self._dirty = False
        return path

    def build_index_shard(self, leaf: str) -> Path:
        """Append hash→leaf mappings for one leaf into a shard file (offline)."""
        self.open_leaf(leaf)
        shard = self.index_dir / f"{_safe_leaf(leaf)}.idx"
        rows = []
        for ghash in self._entries:
            rows.append(ghash + _safe_leaf(leaf).encode("utf-8")[:64].ljust(64, b"\0"))
        shard.write_bytes(b"".join(rows))
        return shard

    def stats(self) -> dict[str, Any]:
        n_files = len(list(self.leaves_dir.glob("*.ghash")))
        size = sum(p.stat().st_size for p in self.leaves_dir.glob("*.ghash"))
        return {
            "path": str(self.root),
            "epoch": self.epoch,
            "leaf_files": n_files,
            "bytes": size,
            "world_size_estimate_gib": WORLD_SIZE_ESTIMATE,
            "open_leaf": self._leaf,
            "open_entries": len(self._entries),
        }

    def close(self) -> None:
        if self._dirty:
            self.flush_leaf()
        self._entries.clear()
        self._leaf = None


def open_geometry_cache(*, zoom: int | None = None, leaf: str | None = None) -> GeometryResultCache | None:
    enabled = os.environ.get("NAVI_MWM_GEOMETRY_CACHE_ENABLE", "").strip()
    path_raw = os.environ.get("NAVI_MWM_GEOMETRY_CACHE", "").strip()
    if enabled in ("0", "false", "no", "off"):
        return None
    if not path_raw and enabled not in ("1", "true", "yes", "on"):
        return None
    z = zoom if zoom is not None else int(os.environ.get("NAVI_MWM_MAPTERHORN_ZOOM", str(DEFAULT_ZOOM)) or DEFAULT_ZOOM)
    gc = GeometryResultCache(zoom=z)
    if leaf:
        gc.open_leaf(leaf)
    return gc


def iter_roads_jsonl(path: Path) -> Iterator[dict]:
    with path.open() as f:
        for line in f:
            if line.strip():
                yield json.loads(line)
