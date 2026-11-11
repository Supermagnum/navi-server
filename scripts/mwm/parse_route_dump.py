#!/usr/bin/env python3
"""Minimal routes_builder .mapsme.dump parser (RouterResultCode + distance)."""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

CODES = {
    0: "NoError",
    1: "Cancelled",
    2: "NoCurrentPosition",
    3: "InconsistentMWMs",
    4: "RouteNotFound",
    5: "NeedMoreMaps",
    6: "InternalError",
    7: "FileTooOld",
    8: "RouteFileNotExist",
    9: "HasWarnings",
    10: "StartPointNotFound",
    11: "EndPointNotFound",
}


def parse(path: Path) -> dict:
    b = path.read_bytes()
    if len(b) < 16:
        raise ValueError(f"dump too short: {path}")
    (code_i,) = struct.unpack_from("<i", b, 0)
    (build_s,) = struct.unpack_from("<d", b, 4)
    (vtype,) = struct.unpack_from("<i", b, 12)
    off = 16
    (n_cp,) = struct.unpack_from("<Q", b, off)
    off += 8 + n_cp * 16
    (nroutes,) = struct.unpack_from("<Q", b, off)
    off += 8
    distance_m = 0.0
    if nroutes:
        _eta, distance_m = struct.unpack_from("<dd", b, off)
    code = CODES.get(code_i, f"code_{code_i}")
    return {
        "file": str(path),
        "code": code,
        "code_i": code_i,
        "build_s": build_s,
        "vehicle_type": vtype,
        "distance_m": distance_m,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dump")
    ap.add_argument("--require-no-error", action="store_true")
    ap.add_argument("--json-out")
    args = ap.parse_args()
    meta = parse(Path(args.dump))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(meta, indent=2) + "\n")
    print(f"{meta['code']} distance_m={meta['distance_m']}")
    if args.require_no_error and meta["code_i"] != 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
