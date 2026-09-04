# Navi server pack bake (prototype)

Self-contained server-side pipeline on this box. Fetches regional OSM extracts,
bakes Navi indexed packs with the **existing** `navi-indexed-convert` binary
(from `/media/navi/Navi`), validates them, and publishes a blue-green tree.

**Scope:** server-side only. No Android / client contract work lives here.
The app’s direct Geofabrik download + on-device convert path stays the
unconditional fallback and is untouched by this tree.

**Convert tooling:** this pipeline **invokes** `navi-indexed-convert`; it does
not patch or replace it. Ask before changing convert sources in the Navi repo.

---

## Layout

```text
/media/navi/navi-server/
  README.md                 # this file
  docs/client-fetch.md      # future client GET contract + exposure surface
  scripts/                  # independently runnable pipeline steps
    lib/common.sh
    config.example.env
    regions.example.conf
  data/                     # scratch, state, generations, logs, live/previous
    published/              # ONLY tree safe to expose over HTTP (static GET/HEAD)
  http/                     # Apache vhost config (GET/HEAD-only static packs)
  systemd/                  # optional timer units (not enabled by default)
```

| Path | Role |
|---|---|
| `scripts/*.sh` | Fetch / convert / validate / publish / cleanup / weekly orchestrator |
| `data/` | Runtime data root on ZFS `Mypool/navi` |
| `data/published/` | Static pack tree for HTTP (see [`docs/client-fetch.md`](docs/client-fetch.md)) |
| `http/` | Apache vhost: GET/HEAD only, DocumentRoot = `data/published` |
| `systemd/` | `navi-pack-bake.service` + `.timer` (install manually when ready) |
| `/media/navi/Navi` | App repo only — source of `navi-indexed-convert` |

Data root detail:

```text
/media/navi/navi-server/data/
  config.env                 # local settings (copy from scripts/config.example.env)
  regions.conf               # regions to bake (copy from scripts/regions.example.conf)
  scratch/extracts/          # fetched *.osm.pbf
  scratch/convert/<region>/  # convert output before publish
  staging/<generation>/      # in-flight publish
  generations/<generation>/  # immutable published trees (internal)
  published/                 # HTTP DocumentRoot only — packs + current.json
  live -> generations/...    # current (internal)
  previous -> generations/...
  state/regions/<id>/        # ETag / Last-Modified (never served)
  logs/weekly-*.log
```

---

## One-time setup

```bash
/media/navi/navi-server/scripts/setup-server.sh
# Apache (needs your sudo password):
sudo /media/navi/navi-server/scripts/setup-server.sh --apply-apache
# Verify:
/media/navi/navi-server/scripts/setup-server.sh --check

# Build the existing convert binary once in the Navi repo (do not modify its sources):
. "$HOME/.cargo/env"
cd /media/navi/Navi
CARGO_TARGET_DIR=/media/navi/Navi/target cargo build -p navi-ffi --release --bin navi-indexed-convert
```

Hedmark is **not** on Geofabrik (landsdel extracts only). The example region
uses OSM.fr:

`https://download.openstreetmap.fr/extracts/europe/norway/hedmark-latest.osm.pbf`

For a Geofabrik path with published `.md5` checksums, use e.g.
`geofabrik:europe/norway/sorlandet` in `regions.conf`.

---

## Independently testable steps

Run from `/media/navi/navi-server/scripts/`. Each step is usable alone against a
single small region.

### 0. Disk quota

```bash
./check-disk-quota.sh
```

Fails loudly at `NAVI_QUOTA_FAIL_PCT` (default 95%) of the ZFS quota.

### 1. Fetch

```bash
./fetch-extracts.sh hedmark
./fetch-extracts.sh --force hedmark   # ignore ETag / Last-Modified
```

- Downloads into `data/scratch/extracts/<region_id>-latest.osm.pbf`
- Verifies `.md5` when Geofabrik/planet publish one; OSM.fr skips digest with a WARN
- Skips download on HTTP 304 when validators match

### 2. Convert

```bash
./convert-region.sh hedmark
./convert-region.sh --profiles car,foot,bicycle hedmark
./convert-region.sh --elev-dir /path/to/dem hedmark   # bake edge_delta_h_m
```

Invokes existing CLI:

```text
navi-indexed-convert --data-dir … --pbf … [--elev-dir …] [--profiles …]
```

Produces under `data/scratch/convert/<region_id>/`:

- `{stem}.navi-graph-*.rkyv`
- `{stem}.navi-poi-barrier.rkyv`
- `{stem}.navi-wetland.rkyv`
- `{stem}.navi-manifest.json`

Toggle Δh for a full run via `NAVI_BAKE_DELTA_H=1` + `NAVI_ELEV_DIR` in
`data/config.env`.

### 3. Optional town-route bake

```bash
./bake-town-routes.sh --generation-dir /path/to/gen --all
```

No OD builder exists in the Navi tree yet. The step no-ops unless
`NAVI_BAKE_TOWN_ROUTES=1` and `NAVI_TOWN_ROUTE_BIN` point at an executable.
Routes are versioned under the same generation directory.

### 4. Validate

```bash
./validate-packs.sh --from-convert --region hedmark
# or against a staged/published generation:
./validate-packs.sh /media/navi/navi-server/data/generations/<id>
```

Checklist (hard fail on problems; size outliers print `FLAG:` and fail the run):

- [ ] Fetched extracts match published checksums when available
- [ ] Graph, poi-barrier, and wetland packs exist, non-empty, readable
- [ ] Manifest parses and every referenced file exists
- [ ] Pack size / PBF size ratios fall in configured bands (dynamic per region)
- [ ] Vs previous live generation, sizes have not jumped by more than
      `NAVI_SIZE_VS_PREV_MAX_FACTOR` (default ×3)

### 5. Publish (blue-green)

```bash
./publish-packs.sh --region hedmark
./publish-packs.sh --dry-run --region hedmark
```

Assembles `data/staging/<generation>/`, writes `generation-manifest.json`, runs
validate, moves to `data/generations/`, atomically swaps `data/live`, keeps
`data/previous` for rollback, and copies the generation into
`data/published/packs/<region>/<generation>/` (plus `current.json`) for static
HTTP GET. See [`docs/client-fetch.md`](docs/client-fetch.md).

### HTTP static server (read-only)

Packs are served from `data/published/` only — never from `scripts/`,
`scratch/`, `staging/`, or `state/`.

- **Port 80 (Apache):** [`http/apache-navi-packs.conf`](http/apache-navi-packs.conf)
  — GET/HEAD only, DocumentRoot = `data/published`
- Fallback :8097 Python server only if Apache is down

```bash
sudo cp /media/navi/navi-server/http/apache-navi-packs.conf \
        /etc/apache2/sites-available/apache-navi-packs.conf
sudo a2dissite 000-default.conf
sudo a2ensite apache-navi-packs
sudo apache2ctl configtest && sudo systemctl reload apache2
systemctl --user disable --now navi-packs-static.service
```

Full client URL contract + exposure surface: [`docs/client-fetch.md`](docs/client-fetch.md).

### 6. Cleanup

```bash
./cleanup.sh
./cleanup.sh --prune-extracts
```

Prunes old generations beyond `NAVI_KEEP_GENERATIONS` (min 2), stale staging,
and re-checks quota.

### Full smoke test (Hedmark)

```bash
./run-weekly.sh --region hedmark
```

Logs: `/media/navi/navi-server/data/logs/weekly-*.log`. Failures include a clear
`[FAILED]` line.

---

## Scheduling (optional — not fire-and-forget yet)

Units live in `systemd/`. **Do not enable** until a hand-run Hedmark bake
passes validate + publish.

```bash
sudo cp /media/navi/navi-server/systemd/navi-pack-bake.service \
        /media/navi/navi-server/systemd/navi-pack-bake.timer \
        /etc/systemd/system/
sudo systemctl daemon-reload
# After smoke test OK:
# sudo systemctl enable --now navi-pack-bake.timer
# journalctl -u navi-pack-bake.service -n 100
```

Failure notification for the prototype is the `[FAILED]` log line in the weekly
log and the systemd unit result (`systemctl status` / journal).

---

## Size bands vs space estimate

Hedmark-anchored ratios (~0.43 graph, ~0.09 poi, wetland placeholder) from the
Navi repo space-estimate doc are guidance only. The pipeline sizes **dynamically
per region** against wide configurable bands in `data/config.env`.

---

## Guardrails

- Server-side only — no Android / client networking work in this tree
- Existing Geofabrik (or equivalent) on-device path remains the fallback
- Do not overwrite convert tooling without an explicit ask
- Prototype first: single region by hand, then widen `regions.conf`, then enable the timer
