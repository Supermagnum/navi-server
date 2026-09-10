#!/usr/bin/env python3
"""Geofabrik Osmosis-style regional replication helpers (stdlib + osmium CLI).

Applies ``.osc.gz`` diffs to a held ``.osm.pbf`` that carries
``osmosis_replication_*`` header fields (as Geofabrik writes them).

Dependencies:
  - Python 3 stdlib only
  - ``osmium`` CLI (Debian/Ubuntu ``osmium-tool``) — already used/available
    on this host; **not** the Rust ``osmpbf`` crate
  - ``curl`` for HTTP (invoked by callers) / urllib here

Optional alternative (not required): Debian ``pyosmium`` package provides
``pyosmium-up-to-date``. This module deliberately avoids that dependency.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Geofabrik documents ~3 months / ~100 days of regional .osc.gz retention.
DEFAULT_RETENTION_DAYS = 100

STATE_RE = re.compile(
    r"^(?:sequenceNumber=(\d+)|timestamp=(.+))$",
    re.MULTILINE,
)


@dataclass
class ReplicationState:
    sequence: int
    timestamp: str  # ISO-8601, may contain escaped colons from state.txt
    base_url: str


@dataclass
class ApplyResult:
    status: str  # already_current | updated | fallback | error
    reason: str
    held_sequence: Optional[int] = None
    tip_sequence: Optional[int] = None
    diffs_applied: int = 0
    bytes_downloaded: int = 0
    output_pbf: Optional[str] = None


def _log(msg: str) -> None:
    print(f"[geofabrik_replication] {msg}", file=sys.stderr, flush=True)


def seq_to_path(seq: int) -> str:
    """Osmosis replication layout: NNNNNNNNN -> AAA/BBB/CCC."""
    s = f"{seq:09d}"
    return f"{s[0:3]}/{s[3:6]}/{s[6:9]}"


def normalize_timestamp(ts: str) -> str:
    return ts.replace("\\:", ":").strip()


def parse_state_txt(text: str) -> tuple[int, str]:
    seq: Optional[int] = None
    ts: Optional[str] = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        if line.startswith("sequenceNumber="):
            seq = int(line.split("=", 1)[1])
        elif line.startswith("timestamp="):
            ts = normalize_timestamp(line.split("=", 1)[1])
    if seq is None or not ts:
        raise ValueError(f"incomplete state.txt: seq={seq!r} ts={ts!r}")
    return seq, ts


def http_get(url: str, timeout: float = 120.0) -> bytes:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def http_head_ok(url: str, timeout: float = 30.0) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except urllib.error.HTTPError as e:
        return e.code == 200
    except Exception:
        return False


def http_get_size(url: str, timeout: float = 120.0) -> tuple[bytes, int]:
    data = http_get(url, timeout=timeout)
    return data, len(data)


def read_pbf_replication(pbf: Path) -> ReplicationState:
    """Read osmosis_replication_* from a PBF via ``osmium fileinfo -j``.

    Always pass ``--input-format=pbf`` so temp names that do not end in
    ``.osm.pbf`` (or end in ``.partial``) still parse — osmium otherwise
    fails format autodetection and the tip-verify gate false-fails.
    """
    proc = subprocess.run(
        ["osmium", "fileinfo", "--input-format=pbf", "-j", str(pbf)],
        check=True,
        capture_output=True,
        text=True,
    )
    info = json.loads(proc.stdout)
    opt = info.get("header", {}).get("option", {})
    base = opt.get("osmosis_replication_base_url")
    seq_s = opt.get("osmosis_replication_sequence_number")
    ts = opt.get("osmosis_replication_timestamp")
    if not base or seq_s is None or not ts:
        raise ValueError(
            f"{pbf}: missing osmosis_replication_* headers "
            f"(base={base!r} seq={seq_s!r} ts={ts!r})"
        )
    return ReplicationState(
        sequence=int(seq_s),
        timestamp=normalize_timestamp(ts),
        base_url=base.rstrip("/"),
    )


def sequence_matches_tip(actual_sequence: int, tip_sequence: int) -> bool:
    """Pure check: post-diff PBF header must equal the expected tip sequence."""
    return int(actual_sequence) == int(tip_sequence)


def verify_pbf_reaches_tip(pbf: Path, tip: ReplicationState) -> None:
    """Re-read PBF headers and refuse replace if sequence != tip.

    Raises ValueError with a distinct SEQUENCE_TIP_MISMATCH marker so callers
    and logs can key off a clear fail signal (no silent corruption).
    """
    got = read_pbf_replication(pbf)
    if not sequence_matches_tip(got.sequence, tip.sequence):
        raise ValueError(
            f"SEQUENCE_TIP_MISMATCH pbf={pbf} got_seq={got.sequence} "
            f"expected_tip={tip.sequence}"
        )
    _log(
        f"SEQUENCE_TIP_VERIFY=PASS pbf={pbf} seq={got.sequence} tip={tip.sequence}"
    )


def fetch_server_state(base_url: str) -> ReplicationState:
    text = http_get(f"{base_url.rstrip('/')}/state.txt").decode("utf-8", "replace")
    seq, ts = parse_state_txt(text)
    return ReplicationState(sequence=seq, timestamp=ts, base_url=base_url.rstrip("/"))


def discover_tip_sequence(base_url: str, hint: int) -> ReplicationState:
    """Advance past a stale state.txt when newer ``NNN.state.txt`` files exist."""
    base = base_url.rstrip("/")
    tip = hint
    tip_ts = ""
    # Bound probe: Geofabrik daily feeds rarely jump more than a few seqs.
    for _ in range(32):
        nxt = tip + 1
        st_url = f"{base}/{seq_to_path(nxt)}.state.txt"
        if not http_head_ok(st_url):
            break
        text = http_get(st_url).decode("utf-8", "replace")
        tip, tip_ts = parse_state_txt(text)
    if not tip_ts:
        # Fall back to root state.txt timestamp for hint.
        root = fetch_server_state(base)
        if tip == root.sequence:
            return root
        tip_ts = root.timestamp
    return ReplicationState(sequence=tip, timestamp=tip_ts, base_url=base)


def parse_iso_ts(ts: str) -> datetime:
    ts = normalize_timestamp(ts)
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def outside_retention_window(
    held: ReplicationState,
    tip: ReplicationState,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> tuple[bool, str]:
    """True when incremental cannot proceed (age or missing next diff)."""
    age_days = (parse_iso_ts(tip.timestamp) - parse_iso_ts(held.timestamp)).total_seconds() / 86400.0
    if age_days > retention_days:
        return True, f"held timestamp age {age_days:.1f}d > retention {retention_days}d"
    if held.sequence >= tip.sequence:
        return False, "current"
    # If the next sequence after held is already gone, the window was exceeded.
    next_osc = f"{held.base_url}/{seq_to_path(held.sequence + 1)}.osc.gz"
    if not http_head_ok(next_osc):
        return True, f"next diff missing (seq {held.sequence + 1}) — outside retention"
    return False, "ok"


def apply_osc_range(
    input_pbf: Path,
    output_pbf: Path,
    held: ReplicationState,
    tip: ReplicationState,
) -> ApplyResult:
    if held.sequence > tip.sequence:
        return ApplyResult(
            status="error",
            reason=f"held seq {held.sequence} ahead of tip {tip.sequence}",
            held_sequence=held.sequence,
            tip_sequence=tip.sequence,
        )
    if held.sequence == tip.sequence:
        if input_pbf.resolve() != output_pbf.resolve():
            output_pbf.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["cp", "-a", str(input_pbf), str(output_pbf)], check=True)
            try:
                verify_pbf_reaches_tip(output_pbf, tip)
            except Exception:
                output_pbf.unlink(missing_ok=True)
                raise
        else:
            verify_pbf_reaches_tip(input_pbf, tip)
        return ApplyResult(
            status="already_current",
            reason="held sequence matches tip",
            held_sequence=held.sequence,
            tip_sequence=tip.sequence,
            diffs_applied=0,
            bytes_downloaded=0,
            output_pbf=str(output_pbf),
        )

    base = held.base_url
    seqs = list(range(held.sequence + 1, tip.sequence + 1))
    output_pbf.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="navi-osc-") as tmp:
        tmp_path = Path(tmp)
        osc_files: list[Path] = []
        bytes_dl = 0
        for seq in seqs:
            url = f"{base}/{seq_to_path(seq)}.osc.gz"
            _log(f"fetch diff seq={seq} url={url}")
            data, n = http_get_size(url)
            bytes_dl += n
            dest = tmp_path / f"{seq:09d}.osc.gz"
            dest.write_bytes(data)
            # Matching per-seq state (optional integrity).
            st_url = f"{base}/{seq_to_path(seq)}.state.txt"
            try:
                (tmp_path / f"{seq:09d}.state.txt").write_bytes(http_get(st_url))
            except Exception as e:
                _log(f"warn: no state.txt for seq={seq}: {e}")
            osc_files.append(dest)

        applied = tmp_path / "applied.osm.pbf"
        cmd = [
            "osmium",
            "apply-changes",
            str(input_pbf),
            *[str(p) for p in osc_files],
            "-o",
            str(applied),
            "--overwrite",
        ]
        _log(f"osmium apply-changes diffs={len(osc_files)}")
        subprocess.run(cmd, check=True)

        # Rewrite replication headers to tip (apply-changes keeps the old header).
        # Write final output directly to a same-filesystem temp next to output_pbf
        # (os.replace across /tmp → ZFS fails with EXDEV).
        output_pbf.parent.mkdir(parents=True, exist_ok=True)
        final_tmp = output_pbf.with_name(output_pbf.name + ".partial")
        header_cmd = [
            "osmium",
            "cat",
            str(applied),
            "-f",
            "pbf",
            "-o",
            str(final_tmp),
            "--overwrite",
            f"--output-header=osmosis_replication_base_url={tip.base_url}",
            f"--output-header=osmosis_replication_sequence_number={tip.sequence}",
            f"--output-header=osmosis_replication_timestamp={tip.timestamp}",
        ]
        subprocess.run(header_cmd, check=True)
        # Verify tip sequence on the temp file before atomic replace — never
        # promote a PBF whose header did not actually reach the expected tip.
        try:
            verify_pbf_reaches_tip(final_tmp, tip)
        except Exception:
            final_tmp.unlink(missing_ok=True)
            raise
        os.replace(final_tmp, output_pbf)

    return ApplyResult(
        status="updated",
        reason=f"applied seq {held.sequence + 1}..{tip.sequence}",
        held_sequence=held.sequence,
        tip_sequence=tip.sequence,
        diffs_applied=len(seqs),
        bytes_downloaded=bytes_dl,
        output_pbf=str(output_pbf),
    )


def update_pbf(
    input_pbf: Path,
    output_pbf: Path,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> ApplyResult:
    try:
        held = read_pbf_replication(input_pbf)
    except Exception as e:
        return ApplyResult(status="fallback", reason=f"no replication header: {e}")

    try:
        root = fetch_server_state(held.base_url)
        tip = discover_tip_sequence(held.base_url, max(root.sequence, held.sequence))
    except Exception as e:
        return ApplyResult(
            status="error",
            reason=f"failed to read replication server: {e}",
            held_sequence=held.sequence,
        )

    outside, why = outside_retention_window(held, tip, retention_days)
    if outside:
        return ApplyResult(
            status="fallback",
            reason=f"incremental unavailable, falling back to full: {why}",
            held_sequence=held.sequence,
            tip_sequence=tip.sequence,
        )

    try:
        return apply_osc_range(input_pbf, output_pbf, held, tip)
    except Exception as e:
        return ApplyResult(
            status="error",
            reason=f"apply failed: {e}",
            held_sequence=held.sequence,
            tip_sequence=tip.sequence,
        )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input_pbf", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument(
        "--retention-days",
        type=int,
        default=int(os.environ.get("NAVI_GEOFABRIK_DIFF_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)),
    )
    ap.add_argument("--json", action="store_true", help="print ApplyResult as JSON")
    args = ap.parse_args(argv)

    result = update_pbf(args.input_pbf, args.output, retention_days=args.retention_days)
    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        print(
            f"status={result.status} reason={result.reason} "
            f"held={result.held_sequence} tip={result.tip_sequence} "
            f"diffs={result.diffs_applied} bytes={result.bytes_downloaded}"
        )
    if result.status == "error":
        return 2
    if result.status == "fallback":
        return 10
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
