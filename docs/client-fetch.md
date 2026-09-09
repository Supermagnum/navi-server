# Client fetch contract (future Android work — not implemented)

**Status:** design / ops spec only. Nothing on the Android side talks to this
server yet. This document describes how a future client should fetch packs with
plain HTTP(S) GET, and what the server exposes when put on the internet.

**Pipeline:** `/media/navi/navi-server` (see [README](../README.md)).  
**HTTP static root:** `/media/navi/navi-server/data/published` only.  
**Pack binary/JSON formats:** [pack-formats.md](pack-formats.md).

---

## Access model

Clients use **ordinary HTTP(S) GET** (and HEAD). There is:

- no custom protocol
- no auth handshake
- no upload endpoint
- no POST / PUT / DELETE / PATCH that the pack server accepts
- no query parameter that triggers server-side logic

Fetching a manifest or pack file is indistinguishable from fetching any other
static file from a web server.

Default local listener:

```text
http://<host>/
```

(Port **80**, Apache DocumentRoot = `data/published/`. TLS on 443 can be
added later with a certificate; the path layout is unchanged.)

---

## URL layout (matches `publish-packs.sh` → `data/published/`)

After a successful publish, the HTTP DocumentRoot contains only:

```text
/media/navi/navi-server/data/published/
  current.json
  packs/<geofabrik-path>/<generation>/
    manifest.json                 # client-facing: digests + pointers
    checksums.sha256              # sha256 lines for every file in the dir
    <stem>.navi-manifest.json     # same format as on-device convert
    <stem>.navi-graph-*.rkyv
    <stem>.navi-poi-barrier.rkyv
    <stem>.navi-wetland*.rkyv
    …                            # optional town-route files if baked
```

`<geofabrik-path>` is the same slash-separated path the Navi app already uses
in its Download-scope picker (from Geofabrik’s index), e.g. `asia/china/anhui`,
`europe/norway/vestlandet`, `north-america/us/west-virginia`. Bake-time ids
(`asia_china_anhui`) stay internal to convert scratch; publish maps them via
`geofabrik:` lines in `regions*.conf`. Non-Geofabrik sources (custom `url:`)
keep a single-segment bake id.

| URL | Meaning |
|---|---|
| `GET /current.json` | Live generation id + list of regions and their `manifest_url` |
| `GET /packs/<geofabrik-path>/<generation>/manifest.json` | Per-region client manifest (sha256 of each file) |
| `GET /packs/<geofabrik-path>/<generation>/checksums.sha256` | Same digests, `sha256sum` text format |
| `GET /packs/<geofabrik-path>/<generation>/<file>.rkyv` | Pack payload (static file) |
| `GET /packs/<geofabrik-path>/<generation>/<stem>.navi-manifest.json` | Original Navi convert manifest |

Examples (Anhui / China, generation `20260904T120000Z`):

```http
GET /current.json
GET /packs/asia/china/anhui/20260904T120000Z/manifest.json
GET /packs/asia/china/anhui/20260904T120000Z/asia_china_anhui-latest.navi-graph-car.rkyv
```

Apache serves `DocumentRoot = data/published` with no path-specific rules — nested
Geofabrik paths need no vhost change. Smoke / weekly publish goes through
`scripts/publish-packs.sh` or `scripts/lib/publish-safe.sh` (planet smoke /
batched leaves); both resolve the publish directory via
`region_publish_relpath` in `scripts/lib/common.sh`.
---

## DATEX NPRA (optional)

When the operator has enabled DATEX redistribution on navi-server, clients may
also fetch **cached** NPRA DATEX II XML from the same DocumentRoot. This is
**not** part of the pack bake pipeline and is **off by default** on the server.

Full flow and operator setup: [datex-npra.md](datex-npra.md) (see especially
**How clients fetch DATEX data**). Open feeds survey: [datex-open-feeds.md](datex-open-feeds.md). Adding providers: [datex-adding-sources.md](datex-adding-sources.md).

| URL | Meaning |
|---|---|
| `GET /datex/source.json` | NPRA attribution / NLOD note |
| `GET /datex/GetSituation.xml` | Cached situation snapshot |
| `GET /datex/GetTravelTimeData.xml` | Cached travel-time snapshot |
| `GET /datex/GetMeasuredWeatherData.xml` | Cached measured-weather snapshot |
| `GET /datex/GetCCTVSiteTable.xml` | Cached CCTV site table |

```bash
curl -fsS "http://<host>/datex/source.json"
curl -fsS -o situations.xml "http://<host>/datex/GetSituation.xml"
```

Clients must **not** hold NPRA credentials or call vegvesen.no directly for
this path. A **404** means the feature is off or no successful poll has run yet.

---

### `current.json` (shape)

```json
{
  "schema": 1,
  "generation": "20260904T120000Z",
  "created_unix": 1756987200,
  "packs_base": "/packs",
  "layout": "geofabrik-path",
  "regions": [
    {
      "region_id": "asia/china/anhui",
      "bake_id": "asia_china_anhui",
      "generation": "20260904T120000Z",
      "manifest_url": "/packs/asia/china/anhui/20260904T120000Z/manifest.json",
      "has_delta_h": true,
      "bytes": 12345678
    }
  ]
}
```

`region_id` is the Geofabrik path (same string family as the app’s download
picker). Optional `bake_id` is the underscore id used under convert scratch.

### Per-region `manifest.json` (shape)

```json
{
  "schema": 1,
  "generation": "20260904T120000Z",
  "region_id": "asia/china/anhui",
  "bake_id": "asia_china_anhui",
  "stem": "asia_china_anhui-latest",
  "has_delta_h": true,
  "navi_manifest": "asia_china_anhui-latest.navi-manifest.json",
  "files": {
    "asia_china_anhui-latest.navi-manifest.json": {"sha256": "…", "bytes": 1234},
    "asia_china_anhui-latest.navi-graph-car.rkyv": {"sha256": "…", "bytes": 567890},
    "asia_china_anhui-latest.navi-poi-barrier.rkyv": {"sha256": "…", "bytes": 89012},
    "asia_china_anhui-latest.navi-wetland.rkyv": {"sha256": "…", "bytes": 34567}
  }
}
```

No query strings are required or interpreted. Extra query strings, if any, must
be ignored by the client and do nothing on the server (static files).

**Breaking note:** early planet-smoke publishes used flat `packs/<bake_id>/…`
URLs. Those trees are migrated in place with
`scripts/migrate-published-to-geofabrik-paths.sh`. No shipping Android client
consumes this server yet (`client-fetch` is still future work), so there is no
client cache to invalidate.
---

## What a future client should do

All steps are plain GET. **None of this is implemented in the Android app yet.**

1. `GET /current.json` (short timeout).
2. Compare `generation` (and/or per-region generation) to the locally cached
   value for that `region_id`.
3. If current / compatible → stop (cache hit).
4. If stale or missing → `GET /packs/<geofabrik-path>/<generation>/manifest.json`.
5. For each needed pack file listed under `files` (graph profiles, poi-barrier,
   wetland, …): `GET` the file URL next to the manifest; verify `sha256` (and
   size) against the manifest entry. Prefer atomic install into the app data
   dir only after all required digests match.
6. Optionally also fetch `<stem>.navi-manifest.json` if the runtime expects the
   on-device convert manifest shape.

### Fallback (unchanged from today)

On **any** failure — timeout, DNS/TLS failure, non-2xx, incomplete body,
checksum mismatch, format version the binary does not understand, missing
region — the client **falls through to the existing Geofabrik (or equivalent)
download + on-device convert / PBF path**. That path stays available
unconditionally. The server is an optional faster pre-baked path, never a hard
dependency.

| Condition | Behaviour |
|---|---|
| Server unreachable / timeout | Local Geofabrik + convert (today) |
| 404 / incomplete pack set | Same |
| Checksum mismatch | Discard download; same local path |
| No cache hit | Same |

---

## Server exposure surface (internet-safe, read-only)

### What is reachable

Only a listener whose **document root** is:

```text
/media/navi/navi-server/data/published
```

On this box the active listener is Apache site `apache-navi-packs` on
**TCP 80**, DocumentRoot =
`/media/navi/navi-server/data/published`. Config:
[`http/apache-navi-packs.conf`](../http/apache-navi-packs.conf).

(The rootless Python server on 8097 was a prototype; keep
[`http/static-packs-server.py`](../http/static-packs-server.py) only as a
fallback if Apache is down — do not run both.)

Under that root, clients may GET/HEAD:

- `/current.json`
- `/packs/**` (pack tree; listing packs is acceptable; sensitive trees are not
  present under this root)

Default listen: **TCP 80** (standard HTTP). Path layout is identical behind TLS
if/when 443 is configured.

Guarantees on that listener:

- **GET / HEAD only** — other methods → 405 (Python server) or deny (Apache
  `LimitExcept`)
- **No PHP / CGI / SSI / request-driven execution**
- **Document root confinement** — URL paths cannot escape `data/published/`
- **No request body used as input** to any program — there is no dynamic handler

### What is never reachable via this listener

These paths are **outside** DocumentRoot and must stay that way:

| Path | Why |
|---|---|
| `/media/navi/navi-server/scripts/` | Pipeline commands |
| `/media/navi/navi-server/systemd/` | Timer/service units |
| `/media/navi/navi-server/docs/` | Ops/docs (`client-fetch.md`, `pack-formats.md`) |
| `/media/navi/navi-server/data/scratch/` | Extracts + convert scratch |
| `/media/navi/navi-server/data/staging/` | In-flight bake |
| `/media/navi/navi-server/data/state/` | ETag / Last-Modified fetch state |
| `/media/navi/navi-server/data/generations/` | Internal blue-green trees (copied into `published/` for serving) |
| `/media/navi/navi-server/data/logs/` | Bake logs |
| `/media/navi/navi-server/pack-convert-core/` | In-repo convert library |
| `/media/navi/navi-server/target/release/` | `navi-indexed-convert` binary |

There is **no HTTP code path** by which a remote request can:

- start `run-weekly.sh` or any pipeline step
- write a file under the server
- execute a command
- upload an extract or pack

The weekly bake is started only by a human shell, or by a local systemd timer
(not enabled by default) — never by an HTTP request.

### Publish writes only static output

`publish-packs.sh` / `publish_single_region_safe` validate, then:

1. Moves the generation under `data/generations/<id>/` (internal)
2. Atomically updates `data/live` / `data/previous` (internal)
3. **Copies** pack files + writes `manifest.json` / `checksums.sha256` /
   `current.json` under `data/published/packs/<geofabrik-path>/<generation>/`
   (DocumentRoot only). Catalog rebuild walks nested region dirs
   (`scripts/lib/published_tree.py`).

Nothing in the HTTP server invokes publish. Publish does not read request
bodies; it only writes files the static server can later GET.

---

## Enabling / verifying the static server

### Apache on port 80 (default)

```bash
sudo cp /media/navi/navi-server/http/apache-navi-packs.conf \
        /etc/apache2/sites-available/apache-navi-packs.conf
sudo a2dissite 000-default.conf
sudo a2ensite apache-navi-packs
sudo apache2ctl configtest
sudo systemctl reload apache2

# Stop the prototype :8097 listener if it was enabled:
systemctl --user disable --now navi-packs-static.service
```

### Checks

```bash
curl -sI http://127.0.0.1/current.json
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1/current.json
# Expect: GET → 200 (or 404 if nothing published yet); POST → 403
# Nested pack example (after a Geofabrik leaf publish):
# curl -sI http://127.0.0.1/packs/asia/china/anhui/<generation>/manifest.json
```

### Fallback — rootless Python on 8097 (only if Apache is unavailable)

```bash
python3 /media/navi/navi-server/http/static-packs-server.py --port 8097
```

Until the first successful `publish-packs.sh` / `run-weekly.sh`, `/current.json`
may be a placeholder or 404 — clients treat empty/missing packs as “use the
Geofabrik fallback.”

---

## Explicit non-goals

- No Android networking / pack-download code in this pass (or yet).
- No change to the on-device Geofabrik fallback.
- No auth, signing, or CDN billing here (open for a later pass).
- Do not serve `data/` as a whole — only `data/published/`.
