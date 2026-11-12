#!/usr/bin/env python3
"""LEGACY: Pack wetland JSONL into spike2-flat NVSC.zst.

Removed from the validation path. routes_builder now loads sectioned NVSC
(scripts/mwm/hook/navi_wetland_cost.cpp) — the same format clients use.
Kept only for comparing against old spike2 fixtures.

Layout (after zstd): magic NVSC, version u32=1, mwm_sha256[32], mwm_size u64,
count u32, then count * 8-byte records (fid u32, seg u16, dir u8, class u8).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mwm", type=Path, required=True)
    ap.add_argument("--wetland-jsonl", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    try:
        import zstandard as zstd
    except ImportError:
        import zstd  # type: ignore

        def compress(b: bytes) -> bytes:
            return zstd.compress(b, 10)

    else:
        def compress(b: bytes) -> bytes:
            return zstd.ZstdCompressor(level=10).compress(b)

    h = hashlib.sha256()
    size = 0
    with args.mwm.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    sha = h.digest()

    recs = []
    with args.wetland_jsonl.open() as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            recs.append(
                (
                    int(r["feature_id"]),
                    int(r["segment_index"]),
                    int(r["direction"]),
                    int(r["class"]),
                )
            )
    body = bytearray()
    body += b"NVSC"
    body += struct.pack("<I", 1)
    body += sha
    body += struct.pack("<Q", size)
    body += struct.pack("<I", len(recs))
    for fid, seg, direction, cls in recs:
        body += struct.pack("<IHBB", fid, seg, direction, cls)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(compress(bytes(body)))
    print(
        json.dumps(
            {
                "out": str(args.out),
                "records": len(recs),
                "mwm_bytes": size,
                "mwm_sha256": sha.hex(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
