# Navi server pack bake (prototype)

Self-contained server-side pipeline. Fetches regional OSM extracts,
bakes Navi indexed packs with the in-repo `navi-indexed-convert` binary
(`pack-convert-core` / `navi-indexed-convert` crates), validates them, and
publishes a blue-green tree. **No Navi app tree is required to build or run.**

**Scope:** server-side only. No Android / client contract work lives here.
The app’s direct Geofabrik download + on-device convert path stays the
unconditional fallback and is untouched by this tree.

---

## Layout

```text
/media/navi/navi-server/
  README.md                 # this file
  Cargo.toml                # workspace: pack-convert-core + navi-indexed-convert
  pack-convert-core/        # OSM→indexed pack library (standalone extract)
  navi-indexed-convert/     # CLI binary crate
  docs/client-fetch.md      # future client GET contract + exposure surface
  docs/pack-formats.md      # binary/JSON pack formats and how to read them
  scripts/                  # independently runnable pipeline steps
    lib/common.sh
    config.example.env
    regions.example.conf
  data/                     # scratch, state, generations, logs, live/previous
    published/              # ONLY tree safe to expose over HTTP (static GET/HEAD)
  http/                     # Apache vhost config (GET/HEAD-only static packs)
  systemd/                  # bake (opt-in) + daily scrub timer (enabled by setup)
```

| Path | Role |
|---|---|
| `pack-convert-core/` | Convert library (no Navi / UniFFI / HTTP deps) |
| `navi-indexed-convert/` | `navi-indexed-convert` CLI |
| `docs/` | Specs — see [Documentation](#documentation) |
| `scripts/*.sh` | Fetch / convert / validate / publish / scrub / weekly orchestrator |
| `data/` | Runtime data root (any filesystem with enough disk space; ZFS optional) |
| `data/published/` | Static pack tree for HTTP |
| `http/` | Apache vhost: GET/HEAD only, DocumentRoot = `data/published` |
| `systemd/` | `navit-server.service`, bake timer (opt-in), daily `navi-pack-scrub.timer` |

## Documentation

| Doc | Contents |
|---|---|
| [`docs/pack-formats.md`](docs/pack-formats.md) | Binary and JSON pack formats, what they contain, and how to read them |
| [`docs/client-fetch.md`](docs/client-fetch.md) | Future client HTTP GET contract and exposure surface |

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
  logs/weekly-*.log          # atomic: .partial until success
  logs/scrub-*.log
```

The tree is self-maintaining: daily scrub prunes outdated generations, convert
scratch, extracts, staging, stale locks, and old / abandoned `*.partial` logs.
Bake and scrub logs are written atomically (stream to `*.partial`, rename on
success).

---

## One-time setup

```bash
/media/navi/navi-server/scripts/setup-server.sh
# Apache + dedicated service user (needs your sudo password):
sudo /media/navi/navi-server/scripts/setup-server.sh --apply-apache --apply-service
# Verify:
/media/navi/navi-server/scripts/setup-server.sh --check

# Build the in-repo convert binary (pack-convert-core; no Navi tree required):
. "$HOME/.cargo/env"
cd /media/navi/navi-server
cargo build --release -p navi-indexed-convert
```

`--apply-service` creates system user **`navit-server`**, owns `data/`, installs
`navit-server.service` / bake units, and **enables** daily
`navi-pack-scrub.timer` (automatic scrub of outdated files). The weekly bake
timer is **not** enabled by this step.

```bash
# Hand-run a bake as the service user:
sudo systemctl start navit-server.service
journalctl -u navit-server.service -n 100
# Scrub status:
systemctl list-timers navi-pack-scrub.timer
journalctl -u navi-pack-scrub.service -n 50
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

### 0. Disk space

```bash
./check-disk-quota.sh
./check-disk-quota.sh --report-only
```

Gates on **disk space** (filesystem fill via `df` on `NAVI_PACK_ROOT`). Fails
loudly at `NAVI_QUOTA_FAIL_PCT` (default 95%). ZFS is optional: if
`NAVI_ZFS_DATASET` is set and the dataset is visible, reporting uses zfs
properties; otherwise plain `df` is enough. You do not need ZFS to run this
pipeline.

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

Invokes the in-repo CLI (built by `setup-server.sh` or `cargo build --release`):

```text
navi-indexed-convert --data-dir … --pbf … [--elev-dir …] [--profiles …]
```

Produces under `data/scratch/convert/<region_id>/`:

- `{stem}.navi-graph-*.rkyv`
- `{stem}.navi-poi-barrier.rkyv`
- `{stem}.navi-wetland.rkyv`
- `{stem}.navi-manifest.json`

Toggle Δh in `data/config.env` (default **on**):

```bash
NAVI_BAKE_DELTA_H=1          # 0 to disable
NAVI_ELEV_DIR=/media/navi/navi-server/data/elevation
```

DEM layout (`ElevationCache` in `pack-convert-core`): `data/elevation/{copernicus,viewfinder,srtm}/`.
See [`data/elevation/README.md`](data/elevation/README.md). Tiles are not
committed; populate that tree before baking Δh packs for real coverage.

### 3. Optional town-route bake

```bash
./bake-town-routes.sh --generation-dir /path/to/gen --all
```

No OD builder exists in this tree yet. The step no-ops unless
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
HTTP GET. See [`docs/client-fetch.md`](docs/client-fetch.md) and
[`docs/pack-formats.md`](docs/pack-formats.md).

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
Pack binary/JSON formats: [`docs/pack-formats.md`](docs/pack-formats.md).

### 6. Scrub (self-maintaining)

```bash
./cleanup.sh
./cleanup.sh --no-extracts
./cleanup.sh --report-only
```

Prunes old generations beyond `NAVI_KEEP_GENERATIONS` (min 2), aged convert
scratch / extracts / staging, abandoned `*.partial` logs, and reports disk
space. Retention knobs: `NAVI_LOG_KEEP_DAYS`, `NAVI_CONVERT_SCRATCH_KEEP_DAYS`,
`NAVI_EXTRACT_KEEP_DAYS`, `NAVI_STAGING_KEEP_DAYS`. Installed as
`navi-pack-scrub.timer` by setup; also runs at the end of each weekly bake.

### Full smoke test (Hedmark)

```bash
./run-weekly.sh --region hedmark
```

Logs: `/media/navi/navi-server/data/logs/weekly-*.log` (atomic: visible as
`weekly-*.log.partial` until success). Failures leave the `.partial` and a
clear `[FAILED]` line.

---

## Scheduling (optional — not fire-and-forget yet)

Units live in `systemd/`. **Do not enable** until a hand-run Hedmark bake
passes validate + publish.

For multi-region planet smokes, prefer the CPU/RAM-aware parallel runner
(after the sequential in-flight job finishes):

```bash
./scripts/run-planet-smoke-parallel.sh --dry-run --limit 4
./scripts/run-planet-smoke-parallel.sh --resume
# Collision test for generation ids:
./scripts/test-generation-id.sh
```

Concurrency is auto-detected from `nproc` + `MemAvailable` with reserves for
co-resident services (see `NAVI_SMOKE_*` in `config.example.env`).

```bash
# Preferred: install user + units via setup
# (enables daily scrub; does not enable weekly bake):
sudo /media/navi/navi-server/scripts/setup-server.sh --apply-service

# After smoke test OK:
# sudo systemctl enable --now navi-pack-bake.timer
# journalctl -u navit-server.service -n 100
```

Bake and scrub units run as **`navit-server`** (not a login account).

Failure notification for the prototype is the `[FAILED]` log line in the weekly
log (or leftover `*.partial`) and the systemd unit result (`systemctl status` /
journal).

---

## Develop / CI

Rust workspace (Rust **1.98** via `rust-toolchain.toml`):

| Crate | Role |
|---|---|
| `pack-convert-core` | Library: OSM PBF (+ optional DEM) → indexed packs |
| `navi-indexed-convert` | CLI binary used by `scripts/convert-region.sh` |

```bash
cd /media/navi/navi-server
cargo build --release -p navi-indexed-convert
cargo test --workspace
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
```

Unit tests use checked-in mini PBFs under `pack-convert-core/tests/fixtures/`.
`#[ignore]` tests that need large extracts (e.g. ostlandet under
`target/integration-fixtures`) stay local-only.

GitHub Actions (`.github/workflows/ci.yml`) runs fmt, clippy, and
`cargo test --workspace` on every push/PR to `main`. No Navi checkout is
required in CI.

`scripts/lib/common.sh` `resolve_convert_bin()` always builds/uses
`${NAVI_SERVER_ROOT}/target/release/navi-indexed-convert` (no `NAVI_ROOT` /
`NAVI_CONVERT_BIN` fallback).

---

## Size bands vs space estimate

Hedmark-anchored ratios (~0.43 graph, ~0.09 poi, wetland placeholder) are
guidance only. The pipeline sizes **dynamically per region** against wide
configurable bands in `data/config.env`.

---

## Guardrails

- Server-side only — no Android / client networking work in this tree
- Existing Geofabrik (or equivalent) on-device path remains the app fallback
- Convert sources live in-repo (`pack-convert-core` / `navi-indexed-convert`);
  keep bake logic here, not via a Navi sparse checkout
- Prototype first: single region by hand, then widen `regions.conf`, then enable the timer
