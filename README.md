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
| `scripts/*.sh` | Fetch / convert / validate / publish / scrub / weekly + planet-leaf orchestrators |
| `scripts/lib/published_tree.py` | Nested `packs/` catalog rebuild (`current.json`) |
| `scripts/migrate-published-to-geofabrik-paths.sh` | One-shot flat → Geofabrik-path published layout |
| `scripts/run-planet-leaves-batched.sh` | Preferred full-leaf bake (batch, pause on fail) |
| `scripts/start-planet-leaves-screen.sh` | Detached screen launcher (safe wrapper; `--resume`) |
| `scripts/watch-planet-leaves.sh` | Alert-only orchestrator watchdog (no auto-resume) |
| `scripts/probe-geofabrik-health.sh` | Pre-resume Geofabrik HEAD/range probe (does not start a bake) |
| `scripts/prefetch-dem-bbox.py` | Copernicus DEM prefetch (`.poly` ocean-skip + 404 cache) |
| `scripts/gen-geofabrik-leaves.py` | Build `regions.planet.conf` + bboxes from Geofabrik index |
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
| [`docs/incremental-geofabrik.md`](docs/incremental-geofabrik.md) | Geofabrik `.osc.gz` incremental extract updates (**tested on one region; not on weekly/planet schedules yet**) |

Data root detail:

```text
/media/navi/navi-server/data/
  config.env                 # local settings (copy from scripts/config.example.env)
  regions.conf               # weekly / smoke regions (gitignored local copy from regions.example.conf;
                             #   portable terrain_class / skip_reason / band tags live in the example)
  regions.planet.conf        # optional full Geofabrik leaf list (gen-geofabrik-leaves.py)
  regions.planet.conf.bboxes.json
  scratch/extracts/          # fetched <bake_id>-latest.osm.pbf
  scratch/convert/<bake_id>/ # convert output before publish
  staging/<generation>/      # in-flight publish
  generations/<generation>/  # immutable published trees (internal)
  published/                 # HTTP DocumentRoot only — packs + current.json
                             # packs/<geofabrik-path>/<generation>/ (matches app picker)
                             # (+ optional datex/ snapshots when DATEX enabled)
  live -> generations/...    # current (internal)
  previous -> generations/...
  state/regions/<id>/        # ETag / Last-Modified (never served)
  logs/planet-leaves/        # batched planet bake plan / progress / PAUSED
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
# Opt-in Geofabrik .osc.gz update of a held PBF (falls back to full fetch).
# Not used by weekly/planet schedules yet — see docs/incremental-geofabrik.md.
./fetch-extracts.sh --prefer-incremental us_west_virginia
```

- Downloads into `data/scratch/extracts/<region_id>-latest.osm.pbf`
- Soft-fetches the matching Osmosis `.poly` beside the PBF when the provider
  publishes one (Geofabrik `…/<path>.poly`; OSM.fr under `/polygons/…`). Missing
  `.poly` is a WARN only — DEM prefetch fails open and grids the full bbox
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

Planet / weekly runners call `prefetch-dem-bbox.py` with the extract `.poly`
when present so ocean-only 1° Copernicus cells are skipped, and remember
confirmed 404 stems in `data/elevation/copernicus_ocean_404.txt` (runtime
negative cache; not under scratch/). Unit coverage:
`scripts/test-dem-ocean-skip.py`.

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
hedmark  url:https://.../hedmark-latest.osm.pbf  terrain_class=wetland_heavy
africa_guinea_bissau  geofabrik:africa/guinea-bissau  terrain_class=wetland_heavy
```

Hedmark’s tag is inland/mire-targeted (measured wetland ratio ~0.786;
~10% county area as mires per Skog og landskap / Ramsar Hedmarksvidda) —
not a Norway-wide band. Vestlandet (coastal fjord/mountain) measured
~0.214 under the global `0.5` band and needs no override. Guinea-Bissau’s
tag is mangrove / coastal-wetland dense (measured ~0.643 in planet-leaf
batch 1); keep that line in live `data/regions.conf` even when baking from
`regions.planet.conf` — `validate-packs.sh` merges weekly `regions.conf`
overrides on top of the planet leaf list. When a numeric override is in
effect, validate logs `band=[lo,hi] (region override)`; terrain classes
log `band=[lo,hi] (terrain_class=…)`.

**`terrain_class=polar_sparse`.** Separate from numeric band overrides: a
pre-declared geography tag that relaxes **only** `graph_min` to
`NAVI_SIZE_GRAPH_MIN_RATIO_POLAR_SPARSE` (default `0.001`). Global
`NAVI_SIZE_GRAPH_MIN_RATIO=0.10` stays for every untagged region — this is
not a loosened default. POI / wetland / total bands are unchanged.
Use it for extracts whose PBF is dominated by coastline / hydrography /
`natural=*` rather than routable highways — typically polar continents,
Arctic archipelagos, and uninhabited sub-Antarctic leaves (measured
anchors: Antarctica graph ratio ~0.059; Nunavut Qikiqtaaluk ~0.009), and
also the same bbox/PBF-bulk mechanism on non-polar maritime reef extracts
when measured (Coral Sea Islands ~0.012). Cold alone is not enough:
Greenland (~0.697), Iceland, and the Falklands passed the global `0.1`
floor untagged. The name is slightly imprecise on purpose: one tag spans
Arctic / Antarctic / tropical reef climates because the mechanism (huge
Geofabrik bbox of empty ocean/ice/reef, tiny road network in one corner)
is identical — prefer that over near-identical synonym tags. If a fourth
or fifth measured case appears in yet another climate, consider renaming
(e.g. `bbox_sparse`) rather than stacking synonyms. Keep tag lines in
`data/regions.conf` (merge path); do not hand-edit auto-generated
`regions.planet.conf`. Validate logs `band=[0.001,…] (terrain_class=polar_sparse)`.
An explicit `graph_min_ratio=` on the same line still wins over the class floor.

**`terrain_class=wetland_heavy`.** Same shape for the wetland *max* band:
relaxes **only** `wetland_max` to `NAVI_SIZE_WETLAND_MAX_RATIO_WETLAND_HEAVY`
(default `1.0`). Global `0.5` stays for untagged regions. Measured anchors
that motivated the class (all convert-clean, graph/poi/total in band):
Hedmark ~0.786 (inland mire), Guinea-Bissau ~0.643 (mangrove/estuary),
Florida ~0.576 (Everglades / coastal marsh). Spread ~0.21 under a single
`1.0` ceiling — the same value previously used as per-region
`wetland_max_ratio=1.0`, so migrating Hedmark/Guinea-Bissau to the shared
tag is behavior-preserving. “Wet climate” alone is not enough: Brazil Norte
(Amazon) measured wetland ratio **0.054** because rainforest is mostly
`natural=wood`/`forest` in OSM, not `natural=wetland`. Prefer this tag for
mire-heavy inland, mangrove/estuary, coastal-marsh, and major-delta leaves;
validate logs `band=[…,1.0] (terrain_class=wetland_heavy)`.

**`skip_reason=no_road_network` (orchestration skip, not a validate band).**
Distinct from `terrain_class`: those tags relax size bands for regions that
still have **some** routable highways (sparse polar / wetland-heavy).
`skip_reason=no_road_network` is for Geofabrik leaves with **literally zero**
`highway=*` ways — convert correctly hard-fails (`bbox graph empty` in
`bbox_build.rs`) and must not be papered over with a band override. Tag the
region in `data/regions.conf` (same trailing `key=value` style; planet conf
is regenerated and must not be hand-edited for tags).
`run-planet-leaves-batched.sh` reads the marker **before** fetch/convert/
validate/publish, logs
`skip region=… skip_reason=no_road_network evidence=0_highway_ways_in_source_pbf`,
and counts the leaf as **processed** toward batch `N/M` progress (total leaf
count stays the same; published catalog is smaller). Only tag after an
osmium (or equivalent) probe confirms 0 highway ways — inhabited islands
with a handful of streets stay in the normal pipeline.

### 5. Publish (blue-green)

```bash
./publish-packs.sh --region hedmark
./publish-packs.sh --dry-run --region hedmark
```

Assembles `data/staging/<generation>/`, writes `generation-manifest.json`, runs
validate, moves to `data/generations/`, atomically swaps `data/live`, keeps
`data/previous` for rollback, and copies the generation into
`data/published/packs/<geofabrik-path>/<generation>/` (plus `current.json`) for
static HTTP GET.

**Published path layout.** Geofabrik-sourced regions publish under the same
slash-separated path the Navi app’s Download-scope picker uses (from
`geofabrik:` in `regions*.conf`), e.g. bake id `asia_china_anhui` →
`packs/asia/china/anhui/<generation>/`. Convert scratch and extracts still use
underscore bake ids. Custom `url:` / `planet` sources keep a single-segment
bake id under `packs/`. `current.json` lists `region_id` as that publish path
and optional `bake_id` for the underscore id. Legacy flat `packs/<bake_id>/`
trees (early planet-smoke) are moved in place with:

```bash
./migrate-published-to-geofabrik-paths.sh --dry-run
./migrate-published-to-geofabrik-paths.sh
```

Apache needs no path rules for nesting — DocumentRoot is `data/published`.
See [`docs/client-fetch.md`](docs/client-fetch.md) and
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

For multi-region / full-world Geofabrik leaf bakes, prefer the **batched**
runner (sun-order, ~80 GiB scratch budget, publish+clean per batch, **pauses**
on validate failure or crash — does not keep going past a bad region):

```bash
# One-time (or when Geofabrik index changes): regenerate leaf list + bboxes
./scripts/gen-geofabrik-leaves.py -o /media/navi/navi-server/data/regions.planet.conf

# Preferred launcher (detached screen; wrapper always logs END; pause holds):
export SCREENDIR=$HOME/.screen
./scripts/start-planet-leaves-screen.sh
./scripts/start-planet-leaves-screen.sh --resume
# Reattach:  screen -r navi-planet-leaves
# Soft-stop after current region:  touch data/STOP_PLANET_LEAVES
```

**Fetch retries.** Transient upstream failures (HTTP 502/503/504/408/429, connect
timeout, DNS failure, connection reset) are retried inside `fetch-extracts.sh`
with exponential backoff and recovery HEAD probes for up to
`NAVI_FETCH_TRANSIENT_BUDGET_SECS` (default 1 hour) before the orchestrator
writes `PAUSED`. Immediate pause (needs-human): HTTP 404/401/403, validate band
failures, convert crashes, disk-quota gates. Ambiguous codes fail safe to
needs-human. Classification: `scripts/lib/fetch_http_classify.sh`.

**PAUSED hold.** On needs-human / exhausted-budget failures the orchestrator
writes `data/logs/planet-leaves/PAUSED` and **keeps the process alive** (screen
session stays reattachable) instead of exiting. Historically `pause_run` did
`exit 2` and ad-hoc `screen … set -e` wrappers tore the session down, which
looked like a mysterious disappearance — there was no OOM evidence on this host.
Kill the held session when ready, then `--resume`. Optional alert-only watchdog
(does not restart the bake):

```bash
# user timer (copy units, enable):
mkdir -p ~/.config/systemd/user
cp systemd/navi-planet-leaves-watchdog.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now navi-planet-leaves-watchdog.timer
# Alerts append to data/logs/orchestrator-watchdog.log
```

After a **fetch** pause that exhausted the transient budget (or for manual
confidence), you can still run the health probe before `--resume`:

```bash
./scripts/probe-geofabrik-health.sh              # loops until STABLE, then exits
./scripts/probe-geofabrik-health.sh --once       # single round (not enough for GO)
# Log: data/logs/geofabrik-health-probe.log
```

Path-parent composites that already have child leaves in `regions.planet.conf`
(e.g. `north_america_us` when US state leaves exist) are skipped so coverage
is not duplicated. Logs/plan: `data/logs/planet-leaves/` (`batch-plan.json`,
`progress.txt`, `PAUSED`).

The older sequential / parallel smoke runners still exist for ad-hoc probes;
they continue past some failures and are not the preferred full-planet path:

```bash
./scripts/run-planet-smoke.sh --resume
./scripts/run-planet-smoke-parallel.sh --dry-run --limit 4
./scripts/run-planet-smoke-parallel.sh --resume
# Collision / sun-order regressions:
./scripts/test-generation-id.sh
./scripts/test-sun-order-no-log-pollution.sh
```

Concurrency for the parallel smoke is auto-detected from `nproc` +
`MemAvailable` with reserves for co-resident services (see `NAVI_SMOKE_*` and
`NAVI_PLANET_BATCH_SCRATCH_GIB` in `config.example.env`).

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
guidance only. The pipeline sizes **dynamically per region** against
configurable global bands in `data/config.env`, with optional per-region
`key=value` overrides on `regions.conf` lines (see Validate above). For
planet-leaf packing estimates, `run-planet-leaves-batched.sh` assumes ~7.57×
PBF→pack growth when building the ~80 GiB scratch batch plan.

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
