# RepeaterBook mirror (optional plugin)

**Status:** implemented in navi-server, **disabled by default**.  
**Not** part of the pack-bake pipeline. Do not enable fetches without
**written permission** from RepeaterBook covering redistribution.

Navi clients never hold a RepeaterBook key and never contact RepeaterBook.
This host (when enabled + permission-confirmed) fetches the Export API with a
server/backend `app_` token, normalizes rows, and publishes a read-only HTTPS
tree under `/repeaters/`.

---

## Permission status

| Item | Status |
|---|---|
| Written permission obtained | **Operator-owned** — not assumed by this repo |
| Operator attestation flag | `REPEATERBOOK_PERMISSION_CONFIRMED=1` in `data/config.env` |
| Fetch/publish without the flag | **Blocked** (inert exit, no network) |

RepeaterBook requires written permission for mirroring, redistribution, offline
bundling, and secondary APIs. See Step 1 sources below. Set the confirmation
flag only after the letter exists.

### Draft application text (send yourself)

Use the public request form as **“I develop or operate a server/backend app”**.
Suggested body (edit contact / URLs as needed):

> Application name: navi-server-repeaterbook (Navi pack server)  
> Project: Navi — a free, GPL-licensed offline navigation app
> (https://github.com/Supermagnum/navi and https://github.com/Supermagnum/navi-server).  
>  
> We operate a private server that would call the RepeaterBook Export API
> (`exportROW.php`, starting with country=Norway; other countries later via
> config only) about once per week per country. The server holds a single
> server/backend `app_` token (never shipped in the Navi client). Responses are
> normalized and published over read-only HTTPS for Navi app users to download
> into an onboard repeater database for amateur-radio CAT / field use.  
>  
> Clients do not call RepeaterBook and cannot trigger fetches. We will show the
> attribution “Data courtesy of RepeaterBook.com” with a link to
> https://www.repeaterbook.com/ in the published index and manifests, and in the
> client UI.  
>  
> We ask for written permission covering: (1) server-side caching of Export API
> responses; (2) redistribution of the normalized dataset to Navi app users via
> our HTTPS mirror; (3) offline storage and use of that dataset on end-user
> devices. We will not operate a public repeater search website, and we will not
> authorize further redistribution beyond the Navi client’s normal use.  
>  
> Proposed User-Agent (exact string to be confirmed with you):  
> `navi-server-repeaterbook/0.1 (+https://github.com/Supermagnum/navi-server; CONTACT_EMAIL)`  
>  
> Non-commercial / free software. Happy to adjust scope, cadence, or attribution
> to meet your terms.

---

## Step 1 findings (summary)

Sources: [API wiki](https://www.repeaterbook.com/wiki/doku.php?id=api),
[Data use](https://www.repeaterbook.com/wiki/doku.php?id=data_use),
[Terms](https://www.repeaterbook.com/about/legal).

| Topic | Finding |
|---|---|
| Auth | `X-RB-App-Token`; server apps use shared `app_…`; distributed apps use per-user `rbuapp_…` |
| Europe | `GET …/api/exportROW.php?country=Norway` (JSON) |
| Rate limits | Unpublished; back off on HTTP 429; honour `Retry-After` |
| Attribution | “Data courtesy of RepeaterBook.com” + link |
| Redistribution | Written permission required |

**Client choice:** small in-tree HTTPS client (stdlib `urllib`). The PyPI
`repeaterbook` package is unofficial, tied to App #114 / `rbuapp_` + hard-coded
User-Agent, and is not used here.

---

## Design

| Piece | Behaviour |
|---|---|
| Enable | `NAVI_REPEATERBOOK_ENABLED=1` via `--apply-repeaterbook` |
| Permission gate | `REPEATERBOOK_PERMISSION_CONFIRMED=1` set manually after the letter |
| Secrets | `data/secrets/repeaterbook.env` mode `0600`, key `REPEATERBOOK_APP_TOKEN=app_…` |
| Identity | `NAVI_REPEATERBOOK_APP_NAME` / `_VERSION` / `_CONTACT`, or full `NAVI_REPEATERBOOK_USER_AGENT` |
| Countries | `NAVI_REPEATERBOOK_COUNTRIES=Norway` (comma-separated); add Sweden etc. by config only |
| Cadence | `navi-repeaterbook.timer` — Wed 03:30 UTC (offset from Monday pack bake) |
| Failure | Keep last good published data; never publish empty/partial over good |
| Pack bake | Untouched |

App identity strings are **config values**, not hard-coded, so they can match
exactly what RepeaterBook approves. Only `app_` tokens are accepted; `rbuapp_`
is rejected.

### Publish layout

```
GET /repeaters/current.json
GET /repeaters/<slug>/<generation>/manifest.json
GET /repeaters/<slug>/<generation>/repeaters.json.zst
GET /repeaters/<slug>/<generation>/checksums.sha256
```

**Payload format:** zstd-compressed JSON (not SQLite). Justification: single
file, small for country extracts, no SQL dependency for the CAT client, easy
sha256, uses the host `zstd` binary (no Python native wheel).

### Schema (`schema: 1`)

Each record (fields omitted when missing — never invented):

| Field | Meaning |
|---|---|
| `callsign` | Callsign |
| `lat` / `lon` | WGS84 degrees |
| `coord_source` | Upstream hint when present |
| `coord_accuracy` | Reserved; null unless upstream provides it |
| `output_hz` | Output frequency (Hz) |
| `shift_hz` | Signed input−output (Hz) |
| `ctcss_encode_hz` / `ctcss_decode_hz` | CTCSS (Hz) |
| `dcs_encode` / `dcs_decode` | DCS codes (strings) |
| `mode` | Operating mode text |
| `dmr_color_code` | Integer when present |
| `location` | City / landmark text |
| `network` | Optional AllStar/EchoLink/IRLP/WIRES/DMR id map |
| `operational_status` | Upstream status text |
| `repeaterbook_id` | Stable id (`StateID:RptrID` when both exist) |
| `updated_at` | Upstream last-update text |
| `entry_kind` | `repeater` / `aprs` / `simplex` |
| `exclude_recommended` | true for APRS/simplex when marked |

`current.json` and every `manifest.json` include:

```json
"attribution": {
  "text": "Data courtesy of RepeaterBook.com",
  "url": "https://www.repeaterbook.com/"
}
```

---

## Secrets handling

- File: `data/secrets/repeaterbook.env` (gitignored), owner `navit-server`, mode `0600`
- Key never logged (redacted), never written into published files, never sent
  except to RepeaterBook over HTTPS in `X-RB-App-Token`
- Staging under `data/repeaterbook/` is denied by Apache `DirectoryMatch`

---

## Failure behaviour

- Disabled or permission not confirmed → exit 0, no network
- 429 → exponential backoff with jitter, honour `Retry-After`, capped retries;
  then abort country without publishing
- Empty / count outlier vs previous generation → refuse publish, keep last good
- Partial multi-country run → update only successful countries; merge into
  `current.json`; failed countries keep prior generation entries

---

## Tests / dry-run

```bash
cd /media/navi/navi-server   # or the scratch clone
python3 -m unittest plugins.repeaterbook.tests.test_repeaterbook -v
./scripts/test-repeaterbook-mirror.sh
```

Dry-run uses synthetic fixtures only (`*.synthetic.json`) — no RepeaterBook contact.

---

## Rollout commands (operator runs — agent does not sudo)

### A) Update live tree after this PR is merged

```bash
# 1) Pull merged main onto the live checkout (navit-server owns .git)
sudo -u navit-server bash -c 'cd /media/navi/navi-server && git fetch origin main && git merge --ff-only origin/main && git log -1 --oneline'

# 2) Reload Apache if the packs vhost gained /repeaters/ Directory rules
sudo cp /media/navi/navi-server/http/apache-navi-packs.conf \
        /etc/apache2/sites-available/apache-navi-packs.conf
sudo apache2ctl configtest && sudo systemctl reload apache2
```

### B) Create the secret file (mode 0600)

```bash
# Replace the token with your approved server/backend app_ token (not rbuapp_).
sudo -u navit-server bash -c 'umask 077; mkdir -p /media/navi/navi-server/data/secrets
cat > /media/navi/navi-server/data/secrets/repeaterbook.env <<EOF
# RepeaterBook server/backend token — mode 0600. Do not commit.
REPEATERBOOK_APP_TOKEN=app_REPLACE_ME
EOF
chmod 600 /media/navi/navi-server/data/secrets/repeaterbook.env
ls -la /media/navi/navi-server/data/secrets/repeaterbook.env'
```

### C) Config identity + countries (still no permission flag)

```bash
sudo -u navit-server bash -c 'cfg=/media/navi/navi-server/data/config.env
set_kv() { k="$1"; v="$2"; if grep -q "^${k}=" "$cfg"; then sed -i "s|^${k}=.*|${k}=${v}|" "$cfg"; else printf "%s=%s\n" "$k" "$v" >>"$cfg"; fi; }
set_kv NAVI_REPEATERBOOK_ENABLED 1
set_kv REPEATERBOOK_PERMISSION_CONFIRMED 0
set_kv NAVI_REPEATERBOOK_COUNTRIES Norway
set_kv NAVI_REPEATERBOOK_APP_NAME navi-server-repeaterbook
set_kv NAVI_REPEATERBOOK_APP_VERSION 0.1
set_kv NAVI_REPEATERBOOK_CONTACT REPLACE_WITH_APPROVED_CONTACT
set_kv NAVI_REPEATERBOOK_SECRETS_FILE /media/navi/navi-server/data/secrets/repeaterbook.env
grep -E "^(NAVI_REPEATERBOOK_|REPEATERBOOK_PERMISSION)" "$cfg"'
```

Or interactive (writes secrets + enables timer; still leaves permission=0):

```bash
sudo /media/navi/navi-server/scripts/setup-server.sh --apply-repeaterbook
```

### D) After the written permission letter exists

```bash
# Attestation only — do not set this before the letter.
sudo -u navit-server bash -c 'sed -i "s/^REPEATERBOOK_PERMISSION_CONFIRMED=.*/REPEATERBOOK_PERMISSION_CONFIRMED=1/" /media/navi/navi-server/data/config.env
grep "^REPEATERBOOK_PERMISSION_CONFIRMED=" /media/navi/navi-server/data/config.env'
```

### E) Install / enable timer (if not done via --apply-repeaterbook)

```bash
sudo cp /media/navi/navi-server/systemd/navi-repeaterbook.service /etc/systemd/system/
sudo cp /media/navi/navi-server/systemd/navi-repeaterbook.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now navi-repeaterbook.timer
systemctl list-timers navi-repeaterbook.timer
```

### F) First manual fetch for Norway (detached) + follow log

```bash
# Detached oneshot (returns immediately)
sudo systemd-run --uid=navit-server --unit=navi-repeaterbook-manual \
  --working-directory=/media/navi/navi-server \
  --setenv=NAVI_PACK_CONFIG=/media/navi/navi-server/data/config.env \
  /media/navi/navi-server/scripts/repeaterbook-mirror.sh

# Follow log
journalctl -u navi-repeaterbook-manual -f
```

### G) Public check

```bash
curl -fsS https://navigate-me.duckdns.org/repeaters/current.json | python3 -m json.tool
# Verify checksum for the Norway generation listed in current.json:
#   curl the data_url, sha256sum, compare to manifest files.sha256
curl -sI https://navigate-me.duckdns.org/repeaters/current.json
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://navigate-me.duckdns.org/repeaters/current.json
# expect POST → 403
```

### Disable / remove published data

```bash
sudo /media/navi/navi-server/scripts/uninstall-repeaterbook.sh
# also delete secrets + work dir:
sudo /media/navi/navi-server/scripts/uninstall-repeaterbook.sh --purge
```
