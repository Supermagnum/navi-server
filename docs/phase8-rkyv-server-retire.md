# Phase 8 — server `.rkyv` retirement (preparation)

**Do not run destructive steps from this PR.** Operator scripts live under
`/tmp/navi-rkyv-*.sh` on the workstation that prepared Phase 8 (review before
copying to the VPS).

## Keep

- MWM bake / publish (`scripts/mwm/*`, `systemd/navi-mwm-*.{service,timer}`)
- DATEX (`datex-*`, `systemd/navi-datex-*`)
- Attribution / catalog v2 under `published/mwm/`
- Adoption measure (`scripts/phase8/measure-rkyv-adoption.py`)

## Remove (when G5 + gate file allow)

| Item | Path |
|------|------|
| Weekly `.rkyv` bake | `scripts/run-weekly.sh`, `scripts/convert-region.sh`, `scripts/publish-packs.sh`, `systemd/navi-pack-bake.{service,timer}` |
| Pack scrub (`.rkyv` gens) | `scripts/cleanup.sh` pack-gen prune, `systemd/navi-pack-scrub.{service,timer}` (revisit: may still scrub scratch) |
| Freeze / guard | `scripts/lib/rkyv_freeze.py`, `scripts/lib/rkyv_published_guard.py`, tests under `scripts/mwm/tests/test_rkyv_*` |
| Static packs prototype | `http/static-packs-server.py`, `systemd/navi-packs-static.service` if still installed |
| Convert core for packs | `pack-convert-core/` (only if nothing else imports it for MWM) |
| Docs that describe live `.rkyv` publish as current | trim `docs/client-fetch.md` / `docs/pack-formats.md` after cutover |

Published tree size (from `docs/client-migration-from-rkyv.md`): on the order of
**~1.7 TiB** logical / **~656 GiB** compressed for `.rkyv` packs vs **~2.1 GiB**
MWM sidecar tree.

## Operator script order

1. Weeks of `measure-rkyv-adoption.py` → confirm G5.
2. Write gate file containing `G5_OK` (e.g. `/var/tmp/navi-rkyv-retire-g5.ok`).
3. `/tmp/navi-rkyv-retire.sh` (sudo): ZFS snapshot → stub `current.json` → delete packs trees.
4. `/tmp/navi-rkyv-retire-verify.sh` (no sudo).
5. After grace days: `/tmp/navi-rkyv-retire-destroy-snapshot.sh`.
6. On failure: `/tmp/navi-rkyv-retire-rollback.sh`.

## Recommended HTTP behaviour for old clients

**Stub** `/current.json` with empty `regions` + `message` / `update_url`
(see `/tmp/navi-rkyv-stub-current.json`). Classic Navi soft-fails bare **404** into
“Geofabrik download + place index”; already-downloaded `.rkyv` packs continue to
load offline either way. Stub is clearer. Avoid pointing retirement tests at the
live host — use the local mock (`/tmp/navi-rkyv-oldclient-mock.py`).
