# DATEX NPRA redistribution (optional plugin)

## UNVERIFIED — not production-ready until a live NPRA probe

This feature is **implemented** and **off by default**. It is **not**
production-ready until someone with real NPRA DATEX credentials closes the
gaps below. More code review cannot close them.

1. Exact **401 / 403 response body** shape from
   `datex-server-get-v3-1.atlas.vegvesen.no`
2. Whether successful GET always returns a **`Last-Modified`** header
   (presence and casing)
3. **XML namespace / root element** per endpoint in production payloads
4. Whether any **unauthenticated GET** returns something other than 401

Do not treat these as confirmed. The next operator to enable this needs a
real NPRA account (and allowlisted IP/DNS), not another patch cycle.

---

**Status:** implemented in navi-server, **disabled by default**.  
**Not** part of the pack-bake pipeline. Do not enable on a host without an
NPRA DATEX account and an allowlisted IP/DNS.

Navi clients must never hold NPRA credentials. This host polls DATEX II v3.1
snapshots outbound and exposes **cached, unmodified** XML plus attribution
metadata on the existing read-only static DocumentRoot.

The Navi-tree client spec path `docs/plugins/datex-npra-client.md` was
referenced by the task but was **not present** in the workspace at
implementation time; behaviour below follows the server task brief plus
public NPRA DATEX documentation.

---

## Off by default (hard gate)

| Mechanism | Behaviour when `NAVI_DATEX_NPRA_ENABLED` is unset/`0` |
|---|---|
| `scripts/datex-npra-poll.sh` | Exits 0 before importing poller / reading secrets |
| `plugins/datex_npra.poll` | Same enable check first; no credential load, no HTTP |
| `setup-server.sh` (normal, interactive) | Asks “Do you want to set up a DATEX provider?”; **no** leaves DATEX off; **yes** asks username + password and enables |
| `setup-server.sh` (non-interactive) | Does **not** install DATEX units or ask for secrets |
| Apache | `/datex/` 404s until the poller writes files; listing disabled |
| `run-weekly.sh` | Untouched |

Enable with an interactive TTY (full setup offers the same prompts):

```bash
sudo /media/navi/navi-server/scripts/setup-server.sh --apply-datex
```

Prompts: yes/no for DATEX, then username and password (confirm). Passwords are
read with `read -s` (no terminal echo), are not written to setup `log()` lines,
and are not placed in shell history (`set +o history` for the prompt section).
Secrets go only to `data/secrets/datex_npra.env` (mode `0600`). Other DATEX
settings use defaults; set a real `NAVI_DATEX_NPRA_USER_AGENT` contact in
`data/config.env` if the placeholder remains.

Uninstall (stops timer, removes units, strips cron crumbs, removes published
cache; `--purge` also deletes secrets/state):

```bash
sudo /media/navi/navi-server/scripts/uninstall-datex-npra.sh
sudo /media/navi/navi-server/scripts/uninstall-datex-npra.sh --purge
```

---

## Configuration (`data/config.env`)

Defaults ship in `scripts/config.example.env` and `scripts/datex-npra.env.example`.
The example is a **template**: `NAVI_DATEX_NPRA_ENABLED=0`, no username/password
fields, and a placeholder User-Agent contact string only.

| Key | Default | Meaning |
|---|---|---|
| `NAVI_DATEX_NPRA_ENABLED` | `0` | Master switch |
| `NAVI_DATEX_NPRA_BASE_URL` | `https://datex-server-get-v3-1.atlas.vegvesen.no` | DATEX node |
| `NAVI_DATEX_NPRA_ENDPOINTS` | four GET snapshot names | Comma-separated |
| `NAVI_DATEX_NPRA_POLL_INTERVAL_SECS` | `300` | Cadence hint / jitter base |
| `NAVI_DATEX_NPRA_USE_IF_MODIFIED_SINCE` | `1` | Conditional GET |
| `NAVI_DATEX_NPRA_USER_AGENT` | quoted placeholder contact | Sent on upstream GET |
| `NAVI_DATEX_NPRA_SECRETS_FILE` | `data/secrets/datex_npra.env` | Mode `0600` |

### Credentials (priority)

1. Environment: `NAV_DATEX_USERNAME` / `NAV_DATEX_PASSWORD`
2. Secrets file (mode `0600`, gitignored) with the same keys
3. **No** interactive prompt in the poller daemon (setup owns prompting)

If credentials are missing while enabled, the poller **fails closed** and does
not pull.

Operator messages (journal only — never on the public static surface):

| Condition | Message |
|---|---|
| Missing credentials | `DATEX NPRA: credentials not configured. Set NAV_DATEX_USERNAME and NAV_DATEX_PASSWORD, or provide a mode-0600 secrets file (NAVI_DATEX_NPRA_SECRETS_FILE).` |
| HTTP 401 | `DATEX NPRA: authentication failed (HTTP 401 Unauthorized). Check NAV_DATEX_USERNAME / NAV_DATEX_PASSWORD.` |
| HTTP 403 | `DATEX NPRA: access forbidden (HTTP 403 Forbidden). Account may lack permission for this endpoint, or the client IP/DNS is not allowlisted by NPRA.` |

Exact upstream **response body** text for 401/403 remains **UNVERIFIED** (see top).

---

## Endpoints (HTTP GET snapshots)

Upstream path pattern:

`{base_url}/datexapi/{Endpoint}/pullsnapshotdata`

Default endpoints:

- `GetSituation`
- `GetTravelTimeData`
- `GetMeasuredWeatherData`
- `GetCCTVSiteTable`

SOAP filtered pulls are **out of scope**.

---

## How clients fetch DATEX data

Clients use the **same plain HTTP(S) GET** model as packs: no auth, no query
params that trigger server logic, and **no NPRA credentials**. If DATEX is
disabled (the default) or the poller has not yet written a successful cache,
these URLs return **404**.

Base URL is the pack server DocumentRoot (port 80 locally; TLS optional later):

```text
http://<host>/datex/
```

| Step | Client action |
|---|---|
| 1 | `GET /datex/source.json` — confirm NPRA attribution / NLOD note; abort if 404 |
| 2 | `GET /datex/<Endpoint>.xml` for each needed snapshot (see table below) |
| 3 | Treat bodies as **unmodified DATEX II XML** from NPRA; do not strip attribution |
| 4 | Prefer `If-Modified-Since` / `ETag` on later polls if the server sends them (optional) |

### URLs

| Method | Path | Content-Type (typical) | Notes |
|---|---|---|---|
| `GET` / `HEAD` | `/datex/source.json` | `application/json` | Attribution metadata written by the poller |
| `GET` / `HEAD` | `/datex/GetSituation.xml` | `application/xml` | Traffic situations / roadworks / closures |
| `GET` / `HEAD` | `/datex/GetTravelTimeData.xml` | `application/xml` | Travel-time measurements |
| `GET` / `HEAD` | `/datex/GetMeasuredWeatherData.xml` | `application/xml` | Measured weather |
| `GET` / `HEAD` | `/datex/GetCCTVSiteTable.xml` | `application/xml` | CCTV site table |

### Examples

```bash
# Attribution first (always acknowledge NPRA when redistributing further)
curl -fsS "http://<host>/datex/source.json"

# Situation snapshot
curl -fsS -o situations.xml "http://<host>/datex/GetSituation.xml"

# Existence / freshness check without downloading the body
curl -fsSI "http://<host>/datex/GetSituation.xml"
```

```http
GET /datex/source.json HTTP/1.1
Host: <host>

GET /datex/GetSituation.xml HTTP/1.1
Host: <host>
```

### Client rules

- **Do not** call `vegvesen.no` / the DATEX node from the app; credentials stay on navi-server.
- **Do not** send NPRA usernames, passwords, or `Authorization` headers to navi-server.
- **Do not** append filter query strings hoping to change what was polled — the server serves files only.
- On **404**, treat DATEX as unavailable (feature off or no successful poll yet), not as an auth error.
- POST / PUT / DELETE / PATCH are rejected by the vhost (same as packs).

Also listed under [client-fetch.md](client-fetch.md#datex-npra-optional).

---

## Caching and on-disk layout

Internal state/cache (not served): `data/datex_npra/` and `data/secrets/`  
Client-facing (DocumentRoot): `data/published/datex/`

Apache (`http/apache-navi-packs.conf`): DocumentRoot stays `data/published`;
`Options -Indexes` on the tree; dedicated `<Directory …/published/datex>` also
disables indexes and denies `*.partial` / `*.env` / script-like suffixes.
`data/secrets` and `data/datex_npra` are denied via `DirectoryMatch`.

No live proxy: client requests never influence upstream query parameters.
Inbound surface remains GET/HEAD-only via the existing Apache vhost.

---

## Logging

- Never log `Authorization` headers or username/password values
- Never log full XML at INFO/WARN; only endpoint, status, byte count, not_modified/backoff
- Auth failure detail is for operators (`journalctl -u navi-datex-npra.service`)

---

## Tests

```bash
cd /media/navi/navi-server
python3 -m unittest plugins.datex_npra.tests.test_datex_npra -v
```

Includes a short-circuit test that proves **zero** network opener calls when
disabled. No test performs a live authenticated pull.
