#!/usr/bin/env python3
"""CLI: download one CoMaps leaf from a discovery.json."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT / "lib"))
from comaps_release import LeafInfo, download_leaf  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--discovery", type=Path, required=True)
    ap.add_argument("--leaf", required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    disc = json.loads(args.discovery.read_text())
    meta = disc["leaves"][args.leaf]
    download_leaf(
        mirrors=disc["mirrors"],
        map_series=disc["map_series"],
        version=int(disc["version"]),
        leaf=LeafInfo(
            id=meta["id"],
            size=int(meta["size"]),
            sha1_base64=meta["sha1_base64"],
            affiliations=list(meta.get("affiliations") or []),
        ),
        dest=args.out,
    )
    print(json.dumps({"leaf": args.leaf, "out": str(args.out), "bytes": args.out.stat().st_size}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
