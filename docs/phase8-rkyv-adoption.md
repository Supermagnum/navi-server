# Phase 8 — measuring `.rkyv` vs `/mwm/` adoption

Read-only. Run on the Apache host that serves
`https://navigate-me.duckdns.org` (DocumentRoot = `data/published/`).

## Script

```bash
# From a navi-server checkout on the host (no sudo):
./scripts/phase8/measure-rkyv-adoption.py \
  --log-glob '/var/log/apache2/access.log*' \
  --out /var/tmp/navi-rkyv-adoption

# Stable client hashes across weeks (store salt privately):
export NAVI_ADOPTION_IP_SALT='…'
./scripts/phase8/measure-rkyv-adoption.py --out /var/tmp/navi-rkyv-adoption
```

Outputs under `--out`:

| File | Content |
|------|---------|
| `daily.csv` | Per day × class (`rkyv` / `mwm` / `other`): reqs, unique clients, bytes, top UA versions |
| `weekly-report.md` | ISO-week rollup + G5 hint |
| `summary.json` | Machine-readable one-shot summary |

Stdout prints a one-line summary suitable for log aggregation.

## Classification

| Class | URI match |
|-------|-----------|
| `rkyv` | `/current.json`, `/packs/…`, or any path ending in `.rkyv` (not under `/mwm/`) |
| `mwm` | `/mwm` or `/mwm/…` |
| `other` | Everything else (DATEX, landing page, …) — listed in CSV, not in G5 totals |

Unique clients = first 16 hex chars of `SHA-256(salt \|\| NUL \|\| ip)`. Raw IPs are never written.

## Optional weekly timer (user-installed)

A sample user systemd timer is in
`scripts/phase8/navi-rkyv-adoption.{service,timer}` and mirrored under
`/tmp/navi-rkyv-adoption-*.service` for review. Install only after confirming
log path and salt:

```bash
mkdir -p ~/.config/systemd/user
cp scripts/phase8/navi-rkyv-adoption.service ~/.config/systemd/user/
cp scripts/phase8/navi-rkyv-adoption.timer ~/.config/systemd/user/
# Edit WorkingDirectory / Environment in the service unit first.
systemctl --user daemon-reload
systemctl --user enable --now navi-rkyv-adoption.timer
```

## G5 (retirement gate)

From `docs/mwm-release-plan.md` / client `docs/mwm-release-plan.md`: keep
serving `.rkyv` until request volume is near zero for **several consecutive
weeks**. Suggested numeric hint in the script: average **&lt; 5 `.rkyv`
requests/day** over the last window of days with data, with no upward trend.

The measure script only **hints**. Destructive retirement
(`/tmp/navi-rkyv-retire.sh`) refuses unless an explicit gate file exists
(written by the operator after reviewing weeks of reports).

## Access needed

This machine (dev workstation) typically **cannot** read
`/var/log/apache2/` on the live host. Copy the script to the VPS (or pull
this PR) and run there, or grant read access to the access logs (and rotated
`access.log.*`) for the measuring user. No write access to DocumentRoot is
required for measurement.
