#!/usr/bin/env python3
"""Phase 8 — read-only Apache access-log adoption measure (.rkyv vs /mwm/).

No sudo. Parses Combined (or Combined-like) Apache access logs, including
rotated ``access.log*`` files. Emits:

  - daily CSV / JSONL under an output directory
  - a weekly rollup report (Markdown)
  - a one-line summary on stdout

Classifies each request URI:

  - ``rkyv``     — ``.rkyv`` object, ``/packs/`` tree, or classic ``/current.json``
                   (not under ``/mwm/``)
  - ``mwm``      — any path under ``/mwm/``
  - ``other``    — ignored for the adoption totals (still counted in ``all``)

Unique clients are salted SHA-256 of the remote IP (never raw IPs in output).
Client versions are taken from User-Agent tokens that look like Navi
(``Navi/…``, ``navi-…``, ``no.navi.app/…``) when present.

Usage (on the Apache host whose DocumentRoot serves navigate-me packs)::

  ./scripts/phase8/measure-rkyv-adoption.py \\
      --log-glob '/var/log/apache2/access.log*' \\
      --out /var/tmp/navi-rkyv-adoption

  # Optional salt for stable IP hashes across runs (store privately):
  NAVI_ADOPTION_IP_SALT=… ./scripts/phase8/measure-rkyv-adoption.py …

Exit 0 always on successful parse (including zero matching lines). Exit 2
when no log files match the glob (so timers can alert on misconfig).

G5 threshold (from docs/mwm-release-plan.md): several consecutive weeks with
.rkyv totals near zero (owner: suggest < a handful/day and no upward trend).
This script prints a G5 hint; it does not flip the gate file.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from glob import glob
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit

# Apache Combined: host ident user [date] "METHOD path proto" status bytes "ref" "ua"
COMBINED_RE = re.compile(
    r"^(?P<ip>\S+)\s+\S+\s+\S+\s+\[(?P<time>[^\]]+)\]\s+"
    r'"(?P<method>\S+)\s+(?P<path>\S+)(?:\s+(?P<proto>[^"]*))?"\s+'
    r"(?P<status>\d{3})\s+(?P<bytes>\S+)"
    r'(?:\s+"(?P<ref>[^"]*)"\s+"(?P<ua>[^"]*)")?'
)

# [17/Oct/2026:12:34:56 +0000]
TIME_FMT = "%d/%b/%Y:%H:%M:%S %z"

NAVI_UA_RE = re.compile(
    r"(?i)\b(?:Navi(?:App)?|no\.navi\.app)[/ ](?P<ver>[0-9][0-9A-Za-z.\-+]*)"
)

# Suggested G5: < this many .rkyv reqs/day on average over the last full week.
G5_HINT_MAX_RKYV_PER_DAY = 5


def open_log(path: Path):
    if path.suffix == ".gz" or path.name.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("rt", encoding="utf-8", errors="replace")


def parse_time(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, TIME_FMT)
    except ValueError:
        return None


def classify_path(raw_path: str) -> str:
    """Return ``rkyv``, ``mwm``, or ``other``."""
    path = unquote(urlsplit(raw_path).path or "")
    if not path.startswith("/"):
        path = "/" + path
    lower = path.lower()
    if lower.startswith("/mwm/") or lower == "/mwm":
        return "mwm"
    # Classic catalog + pack tree + .rkyv objects (not under /mwm/).
    if lower == "/current.json" or lower.startswith("/current.json?"):
        return "rkyv"
    if "/packs/" in lower or lower.startswith("/packs"):
        return "rkyv"
    if lower.endswith(".rkyv") or ".rkyv?" in lower or ".rkyv/" in lower:
        return "rkyv"
    return "other"


def hash_ip(ip: str, salt: str) -> str:
    h = hashlib.sha256()
    h.update(salt.encode("utf-8"))
    h.update(b"\0")
    h.update(ip.encode("utf-8"))
    return h.hexdigest()[:16]


def parse_bytes(raw: str) -> int:
    if raw == "-" or raw == "":
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def navi_version(ua: str | None) -> str:
    if not ua:
        return ""
    m = NAVI_UA_RE.search(ua)
    return m.group("ver") if m else ""


def week_key(dt: datetime) -> str:
    # ISO week: YYYY-Www (Monday start)
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def iter_lines(paths: Iterable[Path]):
    for path in paths:
        try:
            with open_log(path) as fh:
                for line in fh:
                    yield path, line.rstrip("\n")
        except OSError as e:
            print(f"warn: cannot read {path}: {e}", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--log-glob",
        default=os.environ.get("NAVI_APACHE_ACCESS_GLOB", "/var/log/apache2/access.log*"),
        help="Glob of Apache access logs (default: /var/log/apache2/access.log*)",
    )
    ap.add_argument(
        "--out",
        default=os.environ.get("NAVI_ADOPTION_OUT", "/var/tmp/navi-rkyv-adoption"),
        help="Output directory for CSV / weekly report",
    )
    ap.add_argument(
        "--salt",
        default=os.environ.get("NAVI_ADOPTION_IP_SALT", "navi-phase8-default-salt"),
        help="Salt for IP hashing (set NAVI_ADOPTION_IP_SALT in production)",
    )
    ap.add_argument(
        "--days",
        type=int,
        default=0,
        help="If >0, only keep records from the last N days (UTC)",
    )
    args = ap.parse_args()

    paths = sorted({Path(p) for p in glob(args.log_glob)}, key=lambda p: p.name)
    # Prefer plain files before .1 / .gz ordering already alphabetical; filter dirs.
    paths = [p for p in paths if p.is_file()]
    if not paths:
        print(
            f"error: no log files match {args.log_glob!r} "
            f"(run on the Apache host, or fix --log-glob)",
            file=sys.stderr,
        )
        return 2

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    cutoff: datetime | None = None
    if args.days > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    # day -> class -> counters
    daily: dict[str, dict[str, dict]] = defaultdict(
        lambda: {
            "rkyv": {"reqs": 0, "bytes": 0, "clients": set(), "versions": defaultdict(int)},
            "mwm": {"reqs": 0, "bytes": 0, "clients": set(), "versions": defaultdict(int)},
            "other": {"reqs": 0, "bytes": 0, "clients": set(), "versions": defaultdict(int)},
        }
    )
    parsed = 0
    skipped = 0

    for _path, line in iter_lines(paths):
        m = COMBINED_RE.match(line)
        if not m:
            skipped += 1
            continue
        dt = parse_time(m.group("time"))
        if dt is None:
            skipped += 1
            continue
        if cutoff is not None and dt < cutoff:
            continue
        klass = classify_path(m.group("path"))
        day = dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        bucket = daily[day][klass]
        bucket["reqs"] += 1
        bucket["bytes"] += parse_bytes(m.group("bytes"))
        client = hash_ip(m.group("ip"), args.salt)
        bucket["clients"].add(client)
        ver = navi_version(m.group("ua"))
        if ver:
            bucket["versions"][ver] += 1
        parsed += 1

    # Write daily CSV
    csv_path = out / "daily.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "date",
                "class",
                "reqs",
                "unique_clients",
                "bytes",
                "top_versions",
            ]
        )
        for day in sorted(daily):
            for klass in ("rkyv", "mwm", "other"):
                b = daily[day][klass]
                vers = sorted(b["versions"].items(), key=lambda kv: (-kv[1], kv[0]))[:5]
                w.writerow(
                    [
                        day,
                        klass,
                        b["reqs"],
                        len(b["clients"]),
                        b["bytes"],
                        ";".join(f"{v}:{c}" for v, c in vers),
                    ]
                )

    # Weekly rollup
    weekly: dict[str, dict[str, dict]] = defaultdict(
        lambda: {
            "rkyv": {"reqs": 0, "bytes": 0, "clients": set(), "days": set()},
            "mwm": {"reqs": 0, "bytes": 0, "clients": set(), "days": set()},
        }
    )
    for day, classes in daily.items():
        dt = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        wk = week_key(dt)
        for klass in ("rkyv", "mwm"):
            b = classes[klass]
            w = weekly[wk][klass]
            w["reqs"] += b["reqs"]
            w["bytes"] += b["bytes"]
            w["clients"] |= b["clients"]
            if b["reqs"]:
                w["days"].add(day)

    report_path = out / "weekly-report.md"
    lines = [
        "# Navi Phase 8 — `.rkyv` vs `/mwm/` adoption",
        "",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        f"Logs: `{args.log_glob}` ({len(paths)} file(s))",
        f"Parsed request lines: {parsed} (skipped non-matching: {skipped})",
        "",
        "Unique clients = salted SHA-256 of remote IP (16 hex chars); raw IPs are not written.",
        "",
        "## Weekly totals",
        "",
        "| Week | .rkyv reqs | .rkyv clients | .rkyv bytes | /mwm/ reqs | /mwm/ clients | /mwm/ bytes |",
        "|------|------------|---------------|-------------|------------|---------------|-------------|",
    ]
    for wk in sorted(weekly):
        r = weekly[wk]["rkyv"]
        m = weekly[wk]["mwm"]
        lines.append(
            f"| {wk} | {r['reqs']} | {len(r['clients'])} | {r['bytes']} "
            f"| {m['reqs']} | {len(m['clients'])} | {m['bytes']} |"
        )

    # G5 hint over the most recent complete ISO week with any data, else last 7 days
    last_days = sorted(daily.keys())[-7:]
    rkyv_last = sum(daily[d]["rkyv"]["reqs"] for d in last_days)
    mwm_last = sum(daily[d]["mwm"]["reqs"] for d in last_days)
    avg_rkyv = (rkyv_last / len(last_days)) if last_days else 0.0
    # Near-zero means avg < threshold (including zero). Empty window = not ok.
    g5_ok = bool(last_days) and avg_rkyv < G5_HINT_MAX_RKYV_PER_DAY

    lines += [
        "",
        "## G5 hint (not authoritative)",
        "",
        f"Last {len(last_days)} day(s) with log data: "
        f".rkyv_reqs={rkyv_last} (avg {avg_rkyv:.2f}/day), mwm_reqs={mwm_last}.",
        f"Suggested threshold: avg .rkyv < {G5_HINT_MAX_RKYV_PER_DAY}/day for several weeks.",
        f"Hint for this window: **{'BELOW threshold' if g5_ok else 'ABOVE threshold / insufficient data'}**.",
        "",
        "Owner still decides G5 after several consecutive weeks with no upward trend.",
        "Gate file for destructive retirement is written separately "
        "(see `/tmp/navi-rkyv-retire.sh`).",
        "",
    ]
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # JSON snapshot for automation
    snapshot = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "log_glob": args.log_glob,
        "files": [str(p) for p in paths],
        "parsed": parsed,
        "skipped": skipped,
        "last_days": last_days,
        "rkyv_reqs_last_window": rkyv_last,
        "mwm_reqs_last_window": mwm_last,
        "rkyv_avg_per_day": round(avg_rkyv, 3),
        "g5_hint_below_threshold": g5_ok,
        "g5_hint_max_rkyv_per_day": G5_HINT_MAX_RKYV_PER_DAY,
    }
    (out / "summary.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")

    one_liner = (
        f"navi-adoption: days={len(daily)} rkyv_last{len(last_days)}d={rkyv_last} "
        f"(avg {avg_rkyv:.1f}/d) mwm_last{len(last_days)}d={mwm_last} "
        f"g5_hint={'ok' if g5_ok else 'not_ok'} out={out}"
    )
    print(one_liner)
    print(f"wrote {csv_path}", file=sys.stderr)
    print(f"wrote {report_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
