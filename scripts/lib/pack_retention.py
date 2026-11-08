"""Per-region published pack generation retention for cleanup scrub.

Modes (NAVI_RETENTION_MODE):
  count — keep the newest NAVI_KEEP_GENERATIONS complete gens per region
  grace — keep the current.json generation forever; keep non-current gens
          until NAVI_GRACE_DAYS after they were superseded; still never keep
          more than NAVI_KEEP_GENERATIONS (cap applies inside the window)

Superseded timestamp choice
---------------------------
``superseded_at`` for generation G is the publish time of the next-newer
complete generation for that region (the one that replaced G). Publish time
is parsed from the leading UTC stamp in the generation directory name
(``YYYYMMDDTHHMMSSZ…``, same prefix ``generation_id`` writes).

Rationale: no publish-side marker is required (scrub/config scope only);
the stamp is set when the newer gen is created and is stable (unlike
directory mtime, which verify/touch can change). A ``superseded_at`` file
marker would be cleaner for clock skew across hosts, but would require
publish changes — out of scope.

Safety: if ``current.json`` is missing/unreadable/invalid, plan no
deletions. Per region, if the current gen's ``manifest.json`` or
``checksums.sha256`` is missing, skip that region. Orphan / retired /
unknown dirs are reported only and never deleted.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from published_tree import (
    complete_gens,
    is_generation_dir,
    iter_published_region_dirs,
)

_GEN_TS_LEN = len("YYYYMMDDTHHMMSSZ")


@dataclass
class GenSize:
    logical_bytes: int
    physical_bytes: int


@dataclass
class Deletion:
    region_id: str
    generation: str
    reason: str
    superseded_at: str | None
    logical_bytes: int
    physical_bytes: int
    path: str


@dataclass
class RegionSkip:
    region_id: str
    reason: str


@dataclass
class ReportOnly:
    kind: str
    region_id: str | None
    path: str
    detail: str


@dataclass
class RetentionPlan:
    mode: str
    keep: int
    grace_days: int
    now_unix: int
    regions_scanned: int
    deletions: list[Deletion] = field(default_factory=list)
    skips: list[RegionSkip] = field(default_factory=list)
    reports: list[ReportOnly] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    abort_reason: str | None = None

    @property
    def total_logical(self) -> int:
        return sum(d.logical_bytes for d in self.deletions)

    @property
    def total_physical(self) -> int:
        return sum(d.physical_bytes for d in self.deletions)


def parse_gen_publish_unix(gen_name: str) -> int | None:
    """Parse leading UTC stamp from a generation directory name."""
    if len(gen_name) < _GEN_TS_LEN or gen_name[_GEN_TS_LEN - 1] != "Z":
        return None
    stamp = gen_name[: _GEN_TS_LEN - 1]  # YYYYMMDDTHHMMSS
    try:
        dt = datetime.strptime(stamp, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return int(dt.timestamp())


def format_unix_iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def measure_dir(path: Path) -> GenSize:
    """Logical = apparent bytes; physical = allocated (du), falling back to walk."""
    if not path.is_dir():
        return GenSize(0, 0)
    logical = -1
    physical = -1
    try:
        out = subprocess.check_output(
            ["du", "-sb", "--apparent-size", str(path)],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        logical = int(out.split()[0])
    except (OSError, ValueError, subprocess.CalledProcessError):
        pass
    try:
        # Allocated blocks in 1-byte units (reflects fs compression when present).
        out = subprocess.check_output(
            ["du", "-s", "-B1", str(path)],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        physical = int(out.split()[0])
    except (OSError, ValueError, subprocess.CalledProcessError):
        pass
    if logical >= 0 and physical >= 0:
        return GenSize(logical_bytes=logical, physical_bytes=physical)
    # Fallback: walk (tests / environments without GNU du).
    logical_b = 0
    physical_b = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = Path(root) / name
            try:
                st = fp.stat()
            except OSError:
                continue
            logical_b += st.st_size
            physical_b += int(st.st_blocks) * 512
    return GenSize(logical_bytes=logical_b, physical_bytes=physical_b)


def load_current_index(published: Path) -> tuple[dict[str, str] | None, str | None]:
    """Return ({region_id: generation}, error). error set => do not delete."""
    cur_path = published / "current.json"
    if not cur_path.is_file():
        return None, f"current.json missing: {cur_path}"
    try:
        raw = cur_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"current.json unreadable/invalid: {exc}"
    if not isinstance(data, dict):
        return None, "current.json root is not an object"
    regions = data.get("regions")
    if not isinstance(regions, list):
        return None, "current.json missing regions[]"
    out: dict[str, str] = {}
    for i, entry in enumerate(regions):
        if not isinstance(entry, dict):
            return None, f"current.json regions[{i}] is not an object"
        rid = entry.get("region_id")
        gen = entry.get("generation")
        if not isinstance(rid, str) or not rid.strip():
            return None, f"current.json regions[{i}] missing region_id"
        if not isinstance(gen, str) or not gen.strip():
            return None, f"current.json regions[{i}] missing generation"
        if rid in out:
            return None, f"current.json duplicate region_id: {rid}"
        out[rid] = gen
    return out, None


def current_gen_complete(region_dir: Path, generation: str) -> str | None:
    """Return skip reason if current gen is unsafe to prune against."""
    gen_dir = region_dir / generation
    if not gen_dir.is_dir():
        return f"current generation dir missing: {generation}"
    if (gen_dir / ".publish_in_progress").exists():
        return f"current generation still marked in-progress: {generation}"
    if not (gen_dir / "manifest.json").is_file():
        return f"current generation manifest.json missing: {generation}"
    if not (gen_dir / "checksums.sha256").is_file():
        return f"current generation checksums.sha256 missing: {generation}"
    try:
        json.loads((gen_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return f"current generation manifest.json invalid: {generation} ({exc})"
    return None


def _sorted_complete(region_dir: Path) -> list[Path]:
    # Newest first (lexicographic on generation_id stamp prefix).
    return complete_gens(region_dir)


def plan_region_count(
    region_id: str,
    region_dir: Path,
    current_gen: str,
    keep: int,
) -> tuple[list[Deletion], list[ReportOnly], list[str]]:
    deletions: list[Deletion] = []
    reports: list[ReportOnly] = []
    issues: list[str] = []
    gens = _sorted_complete(region_dir)
    gen_names = {g.name for g in gens}
    if current_gen not in gen_names:
        issues.append(
            f"{region_id}: current.json generation {current_gen} not among complete gens on disk"
        )

    # Protect current; keep newest `keep` among complete gens, but never drop current.
    protected = {current_gen}
    keep_set = set(protected)
    for g in gens:
        if len(keep_set) >= keep:
            break
        keep_set.add(g.name)

    for g in gens:
        if g.name in keep_set:
            continue
        if g.name == current_gen:
            continue
        sizes = measure_dir(g)
        deletions.append(
            Deletion(
                region_id=region_id,
                generation=g.name,
                reason=f"count_exceeds_keep={keep}",
                superseded_at=None,
                logical_bytes=sizes.logical_bytes,
                physical_bytes=sizes.physical_bytes,
                path=str(g),
            )
        )

    _report_unknown_children(region_id, region_dir, gen_names, reports)
    return deletions, reports, issues


def plan_region_grace(
    region_id: str,
    region_dir: Path,
    current_gen: str,
    keep: int,
    grace_days: int,
    now_unix: int,
) -> tuple[list[Deletion], list[ReportOnly], list[str]]:
    deletions: list[Deletion] = []
    reports: list[ReportOnly] = []
    issues: list[str] = []
    gens = _sorted_complete(region_dir)  # newest first
    gen_names = {g.name for g in gens}
    if current_gen not in gen_names:
        issues.append(
            f"{region_id}: current.json generation {current_gen} not among complete gens on disk"
        )

    # For each gen (except newest), superseded_at = publish time of next-newer.
    # gens[i] is superseded by gens[i-1] when i >= 1.
    publish_unix: dict[str, int | None] = {
        g.name: parse_gen_publish_unix(g.name) for g in gens
    }
    for g in gens:
        if publish_unix[g.name] is None:
            issues.append(
                f"{region_id}: unparseable generation timestamp in name {g.name}"
            )

    grace_secs = grace_days * 86400
    # Candidates to keep: current always; others if still inside grace window.
    retain: list[Path] = []
    delete_by_grace: list[tuple[Path, int | None, str]] = []

    for idx, g in enumerate(gens):
        if g.name == current_gen:
            retain.append(g)
            continue
        if idx == 0:
            # Newer on disk than current.json — do not delete; report.
            issues.append(
                f"{region_id}: disk has newer complete gen {g.name} than current.json {current_gen}"
            )
            retain.append(g)
            continue
        newer = gens[idx - 1]
        superseded_unix = publish_unix.get(newer.name)
        if superseded_unix is None:
            # Cannot prove grace elapsed — keep.
            issues.append(
                f"{region_id}: cannot compute superseded_at for {g.name} "
                f"(newer gen {newer.name} has bad timestamp); keeping"
            )
            retain.append(g)
            continue
        age = now_unix - superseded_unix
        if age < grace_secs:
            retain.append(g)
        else:
            delete_by_grace.append(
                (
                    g,
                    superseded_unix,
                    f"grace_elapsed_days={age / 86400.0:.1f}>{grace_days}",
                )
            )

    # Cap: among retained, never keep more than `keep` (current protected).
    # Drop oldest non-current first.
    retain_sorted = sorted(retain, key=lambda p: p.name, reverse=True)
    keep_set = {current_gen}
    for g in retain_sorted:
        if g.name == current_gen:
            continue
        if len(keep_set) >= keep:
            sizes = measure_dir(g)
            newer_name = None
            # Find next-newer among all gens for superseded_at logging.
            for idx, cand in enumerate(gens):
                if cand.name == g.name and idx > 0:
                    newer_name = gens[idx - 1].name
                    break
            sup = publish_unix.get(newer_name) if newer_name else None
            deletions.append(
                Deletion(
                    region_id=region_id,
                    generation=g.name,
                    reason=f"cap_exceeds_keep={keep}_inside_grace",
                    superseded_at=format_unix_iso(sup),
                    logical_bytes=sizes.logical_bytes,
                    physical_bytes=sizes.physical_bytes,
                    path=str(g),
                )
            )
        else:
            keep_set.add(g.name)

    for g, sup_unix, reason in delete_by_grace:
        if g.name in keep_set:
            continue
        sizes = measure_dir(g)
        deletions.append(
            Deletion(
                region_id=region_id,
                generation=g.name,
                reason=reason,
                superseded_at=format_unix_iso(sup_unix),
                logical_bytes=sizes.logical_bytes,
                physical_bytes=sizes.physical_bytes,
                path=str(g),
            )
        )

    _report_unknown_children(region_id, region_dir, gen_names, reports)
    return deletions, reports, issues


def _report_unknown_children(
    region_id: str,
    region_dir: Path,
    known_gens: set[str],
    reports: list[ReportOnly],
) -> None:
    try:
        children = list(region_dir.iterdir())
    except OSError as exc:
        reports.append(
            ReportOnly(
                kind="region_unreadable",
                region_id=region_id,
                path=str(region_dir),
                detail=str(exc),
            )
        )
        return
    for child in children:
        if not child.is_dir():
            continue
        name = child.name
        if name.startswith("."):
            # In-progress / .deleting leftovers — report only.
            reports.append(
                ReportOnly(
                    kind="dot_dir",
                    region_id=region_id,
                    path=str(child),
                    detail="left alone",
                )
            )
            continue
        if name in known_gens:
            continue
        if is_generation_dir(name):
            # Incomplete gen (no manifest) — leave alone.
            reports.append(
                ReportOnly(
                    kind="incomplete_generation",
                    region_id=region_id,
                    path=str(child),
                    detail="no complete manifest/checksums; left alone",
                )
            )
        else:
            reports.append(
                ReportOnly(
                    kind="unknown_dir",
                    region_id=region_id,
                    path=str(child),
                    detail="not a generation dir; left alone",
                )
            )


def build_plan(
    published: Path,
    *,
    mode: str,
    keep: int,
    grace_days: int,
    now_unix: int | None = None,
) -> RetentionPlan:
    mode_norm = mode.strip().lower()
    if mode_norm not in ("count", "grace"):
        raise ValueError(f"invalid retention mode: {mode!r} (want count|grace)")
    if keep < 1:
        raise ValueError("keep must be >= 1")
    if grace_days < 0:
        raise ValueError("grace_days must be >= 0")

    now = int(time.time()) if now_unix is None else int(now_unix)
    plan = RetentionPlan(
        mode=mode_norm,
        keep=keep,
        grace_days=grace_days,
        now_unix=now,
        regions_scanned=0,
    )

    current_map, err = load_current_index(published)
    if err is not None:
        plan.abort_reason = err
        plan.issues.append(err)
        return plan
    assert current_map is not None

    packs_root = published / "packs"
    if not packs_root.is_dir():
        plan.abort_reason = f"packs root missing: {packs_root}"
        plan.issues.append(plan.abort_reason)
        return plan

    on_disk = {
        d.relative_to(packs_root).as_posix(): d
        for d in iter_published_region_dirs(packs_root)
    }

    # Retired / orphan region dirs: on disk but not in current.json.
    for rid, rdir in sorted(on_disk.items()):
        if rid not in current_map:
            plan.reports.append(
                ReportOnly(
                    kind="orphan_or_retired_region",
                    region_id=rid,
                    path=str(rdir),
                    detail="not listed in current.json; left alone",
                )
            )

    for rid, current_gen in sorted(current_map.items()):
        plan.regions_scanned += 1
        region_dir = packs_root / rid
        if rid not in on_disk:
            plan.skips.append(
                RegionSkip(region_id=rid, reason="region dir missing on disk")
            )
            continue
        skip = current_gen_complete(region_dir, current_gen)
        if skip is not None:
            plan.skips.append(RegionSkip(region_id=rid, reason=skip))
            continue

        if mode_norm == "count":
            dels, reps, issues = plan_region_count(
                rid, region_dir, current_gen, keep
            )
        else:
            dels, reps, issues = plan_region_grace(
                rid, region_dir, current_gen, keep, grace_days, now
            )
        plan.deletions.extend(dels)
        plan.reports.extend(reps)
        plan.issues.extend(issues)

    return plan


def atomic_delete_gen(path: Path) -> Path:
    """Rename generation dir to sibling .deleting-* then remove. Returns trash path."""
    parent = path.parent
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    trash = parent / f".deleting-{path.name}-{stamp}"
    # If a prior crash left a same-named trash, add pid.
    if trash.exists():
        trash = parent / f".deleting-{path.name}-{stamp}-{os.getpid()}"
    path.rename(trash)
    shutil.rmtree(trash, ignore_errors=False)
    return trash


def apply_plan(
    plan: RetentionPlan,
    *,
    dry_run: bool,
    log=print,
) -> int:
    """Apply deletions. Returns number of gens deleted (0 in dry-run)."""
    if plan.abort_reason:
        log(f"pack-retention ABORT: {plan.abort_reason}")
        return 0
    deleted = 0
    for d in plan.deletions:
        log(
            "pack-retention {action} region={region} generation={gen} "
            "superseded_at={sup} reason={reason} "
            "logical_bytes={logb} physical_bytes={phys}".format(
                action="DRY-RUN would delete" if dry_run else "delete",
                region=d.region_id,
                gen=d.generation,
                sup=d.superseded_at or "-",
                reason=d.reason,
                logb=d.logical_bytes,
                phys=d.physical_bytes,
            )
        )
        if dry_run:
            continue
        trash = atomic_delete_gen(Path(d.path))
        log(f"pack-retention removed via {trash.name}")
        deleted += 1
    for s in plan.skips:
        log(f"pack-retention SKIP region={s.region_id} reason={s.reason}")
    for r in plan.reports:
        log(
            f"pack-retention REPORT kind={r.kind} region={r.region_id or '-'} "
            f"path={r.path} detail={r.detail}"
        )
    for issue in plan.issues:
        log(f"pack-retention ISSUE {issue}")
    log(
        "pack-retention summary mode={mode} keep={keep} grace_days={grace} "
        "regions_scanned={n} deletions={d} "
        "logical_bytes={lb} physical_bytes={pb} dry_run={dry}".format(
            mode=plan.mode,
            keep=plan.keep,
            grace=plan.grace_days,
            n=plan.regions_scanned,
            d=len(plan.deletions),
            lb=plan.total_logical,
            pb=plan.total_physical,
            dry=int(dry_run),
        )
    )
    return deleted


def plan_to_dict(plan: RetentionPlan) -> dict:
    return {
        "mode": plan.mode,
        "keep": plan.keep,
        "grace_days": plan.grace_days,
        "now_unix": plan.now_unix,
        "regions_scanned": plan.regions_scanned,
        "abort_reason": plan.abort_reason,
        "deletions": [asdict(d) for d in plan.deletions],
        "skips": [asdict(s) for s in plan.skips],
        "reports": [asdict(r) for r in plan.reports],
        "issues": list(plan.issues),
        "total_logical_bytes": plan.total_logical,
        "total_physical_bytes": plan.total_physical,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    p.add_argument("--published", type=Path, required=True)
    p.add_argument("--mode", choices=("count", "grace"), default="count")
    p.add_argument("--keep", type=int, default=2)
    p.add_argument("--grace-days", type=int, default=7)
    p.add_argument("--now-unix", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json-out", type=Path, default=None)
    p.add_argument("--apply", action="store_true", help="perform deletes (default: dry-run)")
    args = p.parse_args(argv)

    dry_run = not args.apply or args.dry_run
    if args.apply and args.dry_run:
        print("both --apply and --dry-run set; refusing", file=sys.stderr)
        return 2

    plan = build_plan(
        args.published,
        mode=args.mode,
        keep=args.keep,
        grace_days=args.grace_days,
        now_unix=args.now_unix,
    )
    if args.json_out:
        args.json_out.write_text(
            json.dumps(plan_to_dict(plan), indent=2) + "\n", encoding="utf-8"
        )
    apply_plan(plan, dry_run=dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
