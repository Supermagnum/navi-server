# Repeater mirrors (RepeaterBook + RadioID)

**Status:** implemented, **off by default**. Not part of the pack-bake pipeline.

Navi clients never hold upstream keys and never call RepeaterBook or RadioID.
When enabled + permission-confirmed per source, this host fetches, normalizes,
and publishes read-only HTTPS trees under `/repeaters/<source>/…`.

---

## Publish layout (CAT contract)

```
GET /repeaters/current.json
GET /repeaters/<source>/<slug>/<generation>/manifest.json
GET /repeaters/<source>/<slug>/<generation>/repeaters.json.zst
GET /repeaters/<source>/<slug>/<generation>/checksums.sha256
# source = repeaterbook | radioid
```

`current.json` lists **sources**; each source has `attribution`, optional
`licence`, and a `countries[]` array (`manifest_url`, `data_url`, counts,
generation). Each record includes `"source": "repeaterbook"|"radioid"`.

Payload: zstd JSON (not SQLite) — small country extracts, no SQL dependency.

---

## Permission status

| Source | Gate | Without gate |
|---|---|---|
| RepeaterBook | `REPEATERBOOK_PERMISSION_CONFIRMED=1` | no fetch, no publish |
| RadioID | `RADIOID_PERMISSION_CONFIRMED=1` | no fetch, no publish |

Set each flag only after **written permission** covering caching, redistribution
to Navi users, and offline use on devices.

### Draft — RepeaterBook (server/backend app)

Use form option **“I develop or operate a server/backend app”**. See also the
completed field drafts used on the request form (workflow, credentials, rate,
cache, attribution) in git history / operator notes. Core ask:

> Application name: navi-server-repeaterbook. Navi is a free GPL offline
> navigation app. One private server fetches exportROW.php weekly per country
> (start: Norway), holds a server `app_` token (never in the client), publishes
> normalized files over read-only HTTPS for Navi CAT users with attribution
> “Data courtesy of RepeaterBook.com” + https://www.repeaterbook.com/. Asking
> written permission for cache, redistribute to app users, and offline device
> use. No public directory. UA:
> `navi-server-repeaterbook/0.1 (+https://github.com/Supermagnum/navi-server; CONTACT)`.

### Draft — RadioID

Suggested request body (send yourself; contact RadioID via their published
channels / account support):

```
Application / project: navi-server-radioid (Navi pack server)
Project: Navi — free GPL offline navigation
  https://github.com/Supermagnum/navi
  https://github.com/Supermagnum/navi-server

We operate one private server that would, about once per week per configured
country (start: Norway):
  1) GET https://radioid.net/api/dmr/repeater/?country=…&per_page=200 (paginate)
  2) Conditional GET https://database.radioid.net/static/map.json for coordinates
  3) Join on locator; normalize; publish read-only HTTPS files for Navi users

Clients never call RadioID and never hold a token. Optional X-API-Token would
stay server-side only. User-Agent:
  navi-server-radioid/0.1 (+https://github.com/Supermagnum/navi-server; CONTACT)

We ask for written permission covering: (1) server-side caching; (2)
redistribution of a normalized subset to Navi app users via our HTTPS mirror;
(3) offline storage/use on end-user devices. We will not operate a public
repeater feed, bulk export service, or competing directory. Attribution
(provisional until you specify): "Data courtesy of RadioID.net" +
https://www.radioid.net/
```

---

## RadioID technical notes

| Item | Detail |
|---|---|
| API key | **Optional** today (`X-API-Token`). Anonymous works. Obtain via RadioID account/dashboard when available. |
| Join key | **`locator`** — API `locator` == map.json `locator` (== map `id`). Do **not** join on `identity_id`. |
| Coords | From map.json only; API list has none. Missing coords → `position_null: true`, lat/lon null (never guessed). |
| Frequency | MHz strings → `output_hz`. `offset` MHz signed → `shift_hz`. |
| Status | non-active (`off-air`, `inactive`, …) → `exclude_recommended: true` |
| map.json | Conditional GET (ETag / If-Modified-Since); ≤1 download per run; stream-parse; keep configured countries only. |

---

## Setting API keys and permission flags

### RepeaterBook (required `app_` token when fetching)

| | |
|---|---|
| Need key? | Yes — server/backend `app_…` (not `rbuapp_`) after approval |
| Secret file | `/media/navi/navi-server/data/secrets/repeaterbook.env` mode **0600**, owner `navit-server` |
| Key name | `REPEATERBOOK_APP_TOKEN=` |

**Insert (hidden prompt):**

```bash
# Write RepeaterBook app_ token (hidden). Rejects rbuapp_. Owner/mode 0600.
sudo -u navit-server bash -c '
set -euo pipefail; umask 077
SECRETS_DIR=/media/navi/navi-server/data/secrets
SECRETS_FILE=$SECRETS_DIR/repeaterbook.env
mkdir -p "$SECRETS_DIR"; chmod 700 "$SECRETS_DIR"
set +o history; read -rs -p "RepeaterBook app_ token: " TOKEN; echo; set -o history
TOKEN=$(printf "%s" "$TOKEN" | tr -d "[:space:]")
case "$TOKEN" in
  rbuapp_*) echo "REJECTED: rbuapp_ not allowed" >&2; exit 1 ;;
  app_*) ;;
  *) echo "REJECTED: must start with app_" >&2; exit 1 ;;
esac
printf "%s" "$TOKEN" | python3 -c "import pathlib,sys; p=pathlib.Path(sys.argv[1]); t=sys.stdin.read(); p.write_text("# mode 0600\nREPEATERBOOK_APP_TOKEN="+t+"\n")" "$SECRETS_FILE"
chmod 600 "$SECRETS_FILE"
python3 -c "import hashlib,pathlib,stat; p=pathlib.Path("$SECRETS_FILE"); t=[l.split("=",1)[1].strip() for l in p.read_text().splitlines() if l.startswith("REPEATERBOOK_APP_TOKEN=")][0]; st=p.stat(); print("OK",stat.filemode(st.st_mode),"len",len(t),"sha256_12",hashlib.sha256(t.encode()).hexdigest()[:12])"
'
```

**Permission flag** (only after written approval for cache + redistribute to Navi users):

```bash
sudo -u navit-server bash -c 'sed -i "s/^REPEATERBOOK_PERMISSION_CONFIRMED=.*/REPEATERBOOK_PERMISSION_CONFIRMED=1/" /media/navi/navi-server/data/config.env || echo REPEATERBOOK_PERMISSION_CONFIRMED=1 >> /media/navi/navi-server/data/config.env'
```

**Rotate:** re-run the insert block (overwrites file). No service restart required; next timer/manual run picks it up.  
**Remove / disable:** `sudo …/uninstall-repeaterbook.sh [--purge]` or set `NAVI_REPEATERBOOK_ENABLED=0` and delete the secrets file.

**Dry load check (no network):** see operator commands in prior chat / run Python `load_app_token` with `REPEATERBOOK_PERMISSION_CONFIRMED=0`.

### RadioID (optional `X-API-Token`)

| | |
|---|---|
| Need key? | Optional; works anonymously |
| Secret file | `/media/navi/navi-server/data/secrets/radioid.env` mode **0600** |
| Key name | `RADIOID_API_TOKEN=` |
| Obtain | RadioID account / dashboard (confirm on radioid.net when creating a key) |

**Insert (hidden prompt; no prefix rule):**

```bash
# Write optional RadioID X-API-Token (hidden). Empty Enter aborts without writing.
sudo -u navit-server bash -c '
set -euo pipefail; umask 077
SECRETS_DIR=/media/navi/navi-server/data/secrets
SECRETS_FILE=$SECRETS_DIR/radioid.env
mkdir -p "$SECRETS_DIR"; chmod 700 "$SECRETS_DIR"
set +o history; read -rs -p "RadioID X-API-Token (optional): " TOKEN; echo; set -o history
TOKEN=$(printf "%s" "$TOKEN" | tr -d "[:space:]")
[[ -n "$TOKEN" ]] || { echo "No token entered — leaving file unchanged / absent"; exit 0; }
printf "%s" "$TOKEN" | python3 -c "import pathlib,sys; p=pathlib.Path(sys.argv[1]); t=sys.stdin.read(); p.write_text("# mode 0600\nRADIOID_API_TOKEN="+t+"\n")" "$SECRETS_FILE"
chmod 600 "$SECRETS_FILE"
python3 -c "import hashlib,pathlib,stat; p=pathlib.Path("$SECRETS_FILE"); t=[l.split("=",1)[1].strip() for l in p.read_text().splitlines() if l.startswith("RADIOID_API_TOKEN=")][0]; st=p.stat(); print("OK",stat.filemode(st.st_mode),"len",len(t),"sha256_12",hashlib.sha256(t.encode()).hexdigest()[:12])"
'
```

**Permission flag** (only after written approval):

```bash
sudo -u navit-server bash -c 'sed -i "s/^RADIOID_PERMISSION_CONFIRMED=.*/RADIOID_PERMISSION_CONFIRMED=1/" /media/navi/navi-server/data/config.env || echo RADIOID_PERMISSION_CONFIRMED=1 >> /media/navi/navi-server/data/config.env'
```

**Rotate / remove:** same as insert / `uninstall-radioid.sh --purge`.

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| Inert exit, no network | Enable flag 0 or permission flag 0 |
| Auth / secrets error, no network | Missing RepeaterBook `app_` token; or secrets mode not 0600 |
| HTTP 401 / 403 | Bad/revoked token or User-Agent mismatch (RepeaterBook) |
| HTTP 429 | Backoff exhausted; last good data kept |
| Wrong permissions on key file | File readable by group/other — refused |
| RadioID records without lat/lon | Normal when map has null coords; `position_null: true` |

---

## Enable / timer

```bash
sudo /media/navi/navi-server/scripts/setup-server.sh --apply-repeaterbook
sudo /media/navi/navi-server/scripts/setup-server.sh --apply-radioid
```

Shared timer **Wed 03:30 UTC**: `navi-repeaterbook.timer` → `repeater-mirror-all.sh`
(RepeaterBook then RadioID; failure isolation).

### Live tree update after merge

```bash
# Pull merged main onto live checkout
sudo -u navit-server bash -c 'cd /media/navi/navi-server && git fetch origin main && git merge --ff-only origin/main && git log -1 --oneline'
sudo cp /media/navi/navi-server/http/apache-navi-packs.conf /etc/apache2/sites-available/apache-navi-packs.conf
sudo apache2ctl configtest && sudo systemctl reload apache2
```

### Tests / dry-run

```bash
python3 -m unittest plugins.repeaterbook.tests.test_repeaterbook plugins.radioid.tests.test_radioid -v
./scripts/test-repeater-mirror.sh
```
