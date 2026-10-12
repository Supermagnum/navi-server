# Migrating navi-server to another host

Use `scripts/migrate-server.sh` to copy **packs**, **jobs** (state + config +
timer inventory), and **secrets** over **SSH** (`rsync`), then confirm the
destination is in working order.

## Prerequisites

1. Destination has a full **git checkout** of navi-server (scripts/systemd present).
2. SSH key auth to `user@dest` (BatchMode). Prefer a user that can write
   `data/` (or run the script as root so rsync can preserve ownership).
3. On dest, create the service user / units when ready:
   `sudo ./scripts/setup-server.sh --apply-service` (and optional
   `--apply-datex` / `--apply-ddns` / `--apply-repeaterbook` / `--apply-radioid`).

## What is transferred

| Component | Paths |
|-----------|--------|
| `packs` | `data/published/` (packs, `current.json`, datex, repeaters, …) |
| `jobs` | `data/state/`, `config.env`, `regions*.conf`, `CURRENT_GENERATION`, plugin work dirs (`datex_npra`, `radioid`, `repeaterbook`), `ddns.env`; writes `data/state/migrate-jobs-inventory.json` (systemd timer enablement snapshot) |
| `secrets` | `data/secrets/` (forced to mode `700` / files `600`; contents never logged) |

**Not** transferred by default: `scratch/`, `staging/`, `generations/`,
`elevation*/`, or `data/live` / `data/previous` symlinks (absolute links from
the old host break publish — see `docs/vps-prelaunch-checklist.md`).

Optional: `--with-elevation` to include DEM trees (large).

## Commands

```bash
# From the source host (inside the navi-server checkout):
./scripts/migrate-server.sh --dest deploy@new-host
./scripts/migrate-server.sh --dest deploy@new-host:/media/navi/navi-server --dry-run
./scripts/migrate-server.sh --dest deploy@new-host --components packs,secrets
./scripts/migrate-server.sh --dest deploy@new-host --verify-only
```

After a successful migrate, on the destination:

```bash
sudo ./scripts/setup-server.sh --apply-service
# enable optional features to match data/state/migrate-jobs-inventory.json
sudo ./scripts/setup-server.sh --check
./scripts/migrate-server.sh --dest deploy@new-host --verify-only
```

Verify checks include: `published/` + `current.json`, secret modes (not
values), `config.env` sources cleanly, jobs inventory JSON, no dangling
`live`/`previous` absolute symlinks, sample pack checksum when available,
and `setup-server.sh --check`.

## Smoke test

```bash
./scripts/test-migrate-server.sh
```

Uses `--local-dest` (no network). Production path always uses SSH via `--dest`.
