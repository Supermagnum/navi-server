# VPS pre-launch checklist (netcup weekly incremental host)

Target: netcup VPS 2000 G12 (8 vCore / 16 GiB / **512 GB** NVMe). Full planet
bakes stay on the larger box. This 512 GB root is the **minimum supported**
disk envelope.

**MWM-era sizing (media measured 2026-11-24, Europe/Oslo):** a full
production tree is the **navi-server** checkout (~**187 GiB**, including
elevation ~**127 GiB**) plus a planning ~**250 MB** for voice instructions +
sound clips (pre-recorded packs only; Navi voice-guidance design doc;
**0 bytes** on server today) — still ~**187 GiB**, well under **1 GiB** of
audio. On ~**495 GiB** usable on a 512 GB root that leaves ~**308 GiB** free
(~**62%**) — comfortable, **not** tight. **Without** `.rkyv` packs (retired
on media; do not re-seed them). A **serving-only** host (MWM sidecars +
catalog + repeater/DATEX + audio; bake + elevation stay on media) is on the
order of **~15–20 GiB including OS**. See README
[What a new host needs (disk)](../README.md#what-a-new-host-needs-disk).

The held-PBF / ~**331 GiB** published-packs math below is **historical**
(`.rkyv` weekly-incremental envelope). Do not size a new MWM-only VPS as if
those packs still live on the root.

Netcup Copy-On-Write snapshots are external and not scripted from the guest
yet — treat data corruption as unrecoverable until snapshot rollback is wired.

## Held-PBF retention + disk math (historical `.rkyv` weekly)

`cleanup.sh` skips age-delete of extracts by default (`--no-extracts` on
weekly + scrub). It still enforces:

| Knob | VPS default | Role |
|------|-------------|------|
| `NAVI_HELD_PBF_MAX_MIB=512` | drop oversize extracts |
| `NAVI_HELD_PBF_BUDGET_GIB=40` | cap total held PBF bytes (drop largest first) |

Measured full-catalog HEAD sizes (522 regions): holding all = 114.9 GiB.
With the policy ≈ **40 GiB** held (~435 regions stay incremental; the rest
full-fetch). With published `.rkyv` packs ≈**331 GiB** (**historical**) +
scratch/staging ≈25 GiB → **~20% free** on a ~495 GiB usable root. See
`docs/incremental-geofabrik.md` (Held-PBF retention).

Confirm on the VPS:

```bash
grep -E 'NAVI_HELD_PBF_|NAVI_SCRUB_PRUNE' data/config.env || echo 'using common.sh defaults (40 GiB / 512 MiB)'
grep ExecStart /etc/systemd/system/navi-pack-scrub.service
# want: cleanup.sh --skip-quota-gate --no-extracts
```

Do **not** set `NAVI_SCRUB_PRUNE_EXTRACTS=1` or raise the held budget above
what the 512 GB math allows without re-checking free space.

## Other cutover items

- [ ] `NAVI_REGIONS_CONF` points at the published-region set for this host
      (typically `data/regions.planet.conf` or a published subset)
- [x] Weekly path uses `--prefer-incremental` (wired in `run-weekly.sh`)
- [x] Weekly cleanup uses `--no-extracts` (wired in `run-weekly.sh`)
- [x] Scrub timer skips extract age-delete; held-PBF **budget/max** policy
      keeps the 512 GB envelope workable
- [x] Convert concurrency comes only from `WorkerPoolPlan` (weekly does not
      set `NAVI_TILE_BUILD_CONCURRENCY`; confirm config.env on the VPS leaves
      it unset — no pinned value copied from the 32-thread/96 GiB box)
- [x] Checksum re-verify gates produce `CHECKSUM_REVERIFY=PASS|FAIL` before
      trusting a generation / pruning (`pre-swap-live` + `post-write-new`)
- [ ] Snapshot / rollback orchestration still **not** assumed — verify gates are
      the fail signal until netcup snapshots are confirmed and wired
- [ ] DATEX / other timers reviewed separately; do not assume media-server unit
      state
- [ ] Confirm `data/config.env` on the VPS does not export
      `NAVI_TILE_BUILD_CONCURRENCY` (weekly logs a WARN if it is set)
- [ ] When seeding from another host: use `scripts/migrate-server.sh`
      ([`docs/server-migrate.md`](server-migrate.md)) — do **not** copy absolute
      `data/live` / `data/previous` symlinks (media→VPS rsync left a dangling
      `/media/navi/...` link that aborted publish under `set -e` + `readlink -f`)
- [x] 8c/16GiB KVM envelope sim completed 2026-09-10 — see
      `/media/navi/vps-sim/REPORT.md` (RAM clean; disk fixed via held-PBF
      budget policy in-tree)
- [ ] After voice / sound clip packs land: re-run `./check-disk-quota.sh`
      (or `--report-only`) — confirm usage only; ~**250 MB** planning budget
      does not drive capacity warnings on a 512 GB root
