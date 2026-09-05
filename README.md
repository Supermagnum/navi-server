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
  systemd/                  # bake (opt-in), daily scrub, optional DDNS
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
| `systemd/` | `navit-server.service`, bake timer (opt-in), daily scrub, optional `navi-ddns.timer` |

## Documentation

| Doc | Contents |
|---|---|
| [`docs/pack-formats.md`](docs/pack-formats.md) | Binary and JSON pack formats, what they contain, and how to read them |
| [`docs/client-fetch.md`](docs/client-fetch.md) | Future client HTTP GET contract and exposure surface |
| [`docs/datex-npra.md`](docs/datex-npra.md) | Optional DATEX NPRA redistribution (**off by default**) |

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
                             # packs/<geofabrik-path>/<generation>/ (matches app picker)
                             # (+ optional datex/ snapshots when DATEX enabled)
  live -> generations/...    # current (internal)
  previous -> generations/...
  state/regions/<id>/        # ETag / Last-Modified (never served)
  logs/weekly-*.log          # atomic: .partial until success
  logs/scrub-*.log
  secrets/                   # optional DATEX creds (0600; never served)
  datex_npra/                # optional DATEX poller state (never served)
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
timer is **not** enabled by this step. DATEX NPRA redistribution stays **off**
unless you later run `--apply-datex`.

### Dynamic DNS (optional)

Keeps a public hostname pointed at this box’s current IPv4 (DuckDNS, Cloudflare,
or a generic update URL). Credentials stay in `data/ddns.env` (mode `600`), not
in `config.env`.

```bash
cp /media/navi/navi-server/scripts/ddns.env.example \
   /media/navi/navi-server/data/ddns.env
chmod 600 /media/navi/navi-server/data/ddns.env
# edit NAVI_DDNS_PROVIDER / HOSTNAME / TOKEN (and Cloudflare fields if needed)
sudo /media/navi/navi-server/scripts/setup-server.sh --apply-ddns
systemctl list-timers navi-ddns.timer
journalctl -u navi-ddns.service -n 50

# Remove the timer/units (keeps ddns.env unless --purge):
sudo /media/navi/navi-server/scripts/uninstall-ddns.sh
sudo /media/navi/navi-server/scripts/uninstall-ddns.sh --purge
```

### DATEX NPRA redistribution (optional — off by default)

**Role:** navi-server is the **only** place that holds NPRA DATEX credentials and
talks to `vegvesen.no`. Navi clients never see those credentials and never call
the DATEX node — they only download **cached snapshots** from this host over the
same read-only HTTP GET surface used for packs.

**How redistribution works**

```text
  NPRA DATEX II v3.1 node          navi-server                         Navi clients
  (atlas.vegvesen.no)              (this box)
  --------------------             -----------------------             ------------
  GET …/pullsnapshotdata  <----    poller (Basic Auth, outbound)
        XML snapshots      ---->   data/datex_npra/ (private state)
                                   data/published/datex/*.xml   ---->  GET /datex/*.xml
                                   data/published/datex/source.json -> GET /datex/source.json
```

1. **Outbound poll (server only).** When enabled, `navi-datex-npra.timer` runs
   `scripts/datex-npra-poll.sh`, which GETs the configured snapshot endpoints
   (`GetSituation`, `GetTravelTimeData`, `GetMeasuredWeatherData`,
   `GetCCTVSiteTable`) with HTTP Basic Auth. Conditional GET
   (`If-Modified-Since`) and backoff/jitter avoid hammering the node.
2. **Cache, do not proxy.** Successful bodies are written atomically under
   `data/published/datex/` as unmodified XML. Client requests never become
   upstream query parameters — there is no live reverse-proxy to NPRA.
3. **Inbound serve (read-only).** Apache DocumentRoot already includes
   `data/published/`, so clients fetch plain files:
   - `GET /datex/source.json` — NPRA attribution / NLOD note (no secrets)
   - `GET /datex/GetSituation.xml` (and the other endpoint names)
4. **Off by default.** Fresh setup does not install the timer, does not read
   credentials, and does not create `/datex/` until you explicitly enable it.
   Full detail and the live-probe **UNVERIFIED** list:
   [`docs/datex-npra.md`](docs/datex-npra.md).

```bash
# Interactive enable (asks for NPRA username/password; password is not echoed):
sudo /media/navi/navi-server/scripts/setup-server.sh --apply-datex
systemctl list-timers navi-datex-npra.timer
journalctl -u navi-datex-npra.service -n 50
# After a successful poll:
curl -sI http://127.0.0.1/datex/source.json
curl -sI http://127.0.0.1/datex/GetSituation.xml

sudo /media/navi/navi-server/scripts/uninstall-datex-npra.sh [--purge]
```

**Client fetch (same host, no credentials):** see
[`docs/datex-npra.md` — How clients fetch DATEX data](docs/datex-npra.md#how-clients-fetch-datex-data)
and [`docs/client-fetch.md`](docs/client-fetch.md#datex-npra-optional).

```bash
curl -fsS "http://<host>/datex/source.json"
curl -fsS -o situations.xml "http://<host>/datex/GetSituation.xml"
```

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
- [ ] Pack size / PBF size ratios fall in configured bands (global
      `NAVI_SIZE_*` in `config.env`, with optional per-region overrides on
      the `regions.conf` line — see below)
- [ ] Vs previous live generation, sizes have not jumped by more than
      `NAVI_SIZE_VS_PREV_MAX_FACTOR` (default ×3)

**Per-region size-band overrides.** Global wetland max stays at `0.5` so
sparse/alpine extracts (e.g. `europe_alps` ratio `0.004`) still flag
runaways. Genuine outliers get an explicit trailing `key=value` on their
`regions.conf` line (same pattern for graph/poi/total if needed later):

```text
hedmark  url:https://.../hedmark-latest.osm.pbf  wetland_max_ratio=1.0
```

Hedmark’s override is inland/mire-targeted (measured wetland ratio ~0.786;
~10% county area as mires per Skog og landskap / Ramsar Hedmarksvidda) —
not a Norway-wide band. Vestlandet (coastal fjord/mountain) measured
~0.214 under the global `0.5` band and needs no override. When an override
is in effect, validate logs `band=[lo,hi] (region override)`.

### 5. Publish (blue-green)

```bash
./publish-packs.sh --region hedmark
./publish-packs.sh --dry-run --region hedmark
```

Assembles `data/staging/<generation>/`, writes `generation-manifest.json`, runs
validate, moves to `data/generations/`, atomically swaps `data/live`, keeps
`data/previous` for rollback, and copies the generation into
`data/published/packs/<geofabrik-path>/<generation>/` (plus `current.json`) for static
HTTP GET. Paths match the Navi app’s Geofabrik download hierarchy
(e.g. `asia/china/anhui`). See [`docs/client-fetch.md`](docs/client-fetch.md) and
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
./scripts/test-sun-order-no-log-pollution.sh
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
- Optional DATEX NPRA redistribution is **off by default** and must not run
  (no vegvesen.no traffic, no credential read, no `/datex/` files) unless
  explicitly enabled via `setup-server.sh --apply-datex` — see
  [`docs/datex-npra.md`](docs/datex-npra.md)
