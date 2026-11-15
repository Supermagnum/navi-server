# DATEX multi-provider setup

Operator guide for adding national DATEX feeds to navi-server. Clients only
`GET` cached XML under `/datex/<publish_id>/` — credentials never leave the
server.

Related: [datex-npra.md](datex-npra.md) (Vegvesen detail),
[datex-open-feeds.md](datex-open-feeds.md) (open candidates),
[datex-adding-sources.md](datex-adding-sources.md) (legacy plugin stub notes).

---

## Layout

| Path | Role |
|------|------|
| `config/datex/providers/<id>.toml` | Provider config (**no secrets**) |
| `/etc/navi-server/datex/<id>.env` | Secrets (root:`navit-server`, mode **0600**) |
| `data/published/datex/<publish_id>/` | Cached XML + `source.json` (DocumentRoot) |
| `data/datex_state/<id>/status.json` | Last success/error per provider |
| `/datex/providers.json` | Public registry (no secrets) |

**Vegvesen / NPRA** uses config id `no-vegvesen` with **`publish_id = "npra"`**
so client URLs stay `/datex/npra/…` and legacy redirects keep working.

---

## Provider TOML

```toml
id = "example"
publish_id = "example"          # URL path segment; default = id
display_name = "Example NAP"
country = "XX"
attribution = "Required credit text"
licence = "CC BY 4.0"
datex_version = "3.1"           # 2.x or 3.x
auth_type = "none"              # none | basic | api-key | bearer
enabled = true
secrets_file = "/etc/navi-server/datex/example.env"
timeout_secs = 60
# api_key_in = "header"         # or "query"
# api_key_name = "Ocp-Apim-Subscription-Key"
# accept_encoding_gzip = true   # Digitraffic
# legacy_enabled_env = "NAVI_DATEX_NPRA_ENABLED"  # Vegvesen only

[[publications]]
name = "GetSituation"
url = "https://…"
interval_secs = 300
enabled = true
use_conditional = true          # If-Modified-Since / ETag when offered
```

### Auth / secrets file keys

| `auth_type` | Secrets keys (never commit) |
|-------------|-----------------------------|
| `none` | (none) |
| `basic` | `NAV_DATEX_USERNAME`, `NAV_DATEX_PASSWORD` |
| `bearer` | `NAV_DATEX_BEARER_TOKEN` |
| `api-key` | `NAV_DATEX_API_KEY` |

Prefer systemd `LoadCredential=` pointing at `/etc/navi-server/datex/<id>.env`,
or `EnvironmentFile=` with mode 0600. The fetcher also accepts process env of
the same key names (for tests only).

**Licence / attribution:** publish unmodified XML; put required credit in
`attribution` / `licence` (copied into `source.json`). Vegvesen requires
credentials to stay confidential and NPRA to be acknowledged as the source
(NLOD 2.0).

---

## CLI: `scripts/datex-provider.sh`

```bash
./scripts/datex-provider.sh list
./scripts/datex-provider.sh test no-vegvesen
./scripts/datex-provider.sh poll              # all enabled
./scripts/datex-provider.sh poll fi-digitraffic --force

# Add (interactive); secrets via hidden input; test before enable
sudo ./scripts/datex-provider.sh add

# Non-interactive open feed example (Digitraffic)
./scripts/datex-provider.sh add --non-interactive \
  --id fi-digitraffic --name 'Digitraffic' --country FI \
  --datex-version 2.3 --auth-type none --gzip \
  --attribution 'Finnish Transport Infrastructure Agency / Digitraffic' \
  --licence 'CC BY 4.0' \
  --url 'https://tie.digitraffic.fi/api/traffic-message/v1/messages.datex2?inactiveHours=0&situationType=TRAFFIC_ANNOUNCEMENT'

./scripts/datex-provider.sh enable <id>
./scripts/datex-provider.sh disable <id>
./scripts/datex-provider.sh remove <id> -y
sudo ./scripts/datex-provider.sh rotate-credentials <id>
```

`test` reports HTTP 401/403 as bad credentials, 404 as wrong URL, and DATEX
version mismatches — **never** prints secret values.

---

## Examples

### Vegvesen (basic) — shipped as `no-vegvesen.toml`

- Auth: Basic  
- DATEX **3.1** snapshots: Situation, TravelTime, Weather, CCTV site table  
- Client path: **`/datex/npra/`**  
- Enable: `NAVI_DATEX_NPRA_ENABLED=1` + secrets in
  `/etc/navi-server/datex/no-vegvesen.env` (legacy `data/secrets/datex_npra.env`
  still accepted)

### Trafikverket-style (api-key)

```toml
auth_type = "api-key"
api_key_in = "header"
api_key_name = "Ocp-Apim-Subscription-Key"
```

Secrets: `NAV_DATEX_API_KEY=…`

### Digitraffic (open / none)

```toml
auth_type = "none"
datex_version = "2.3"
accept_encoding_gzip = true
```

No secrets file. Always re-check licence terms before production enable.

---

## Polling

`navi-datex-npra.timer` runs `scripts/datex-npra-poll.sh` →
`scripts/datex-poll-all.sh`, which polls **every enabled** provider. Per
publication `interval_secs` and conditional GET still apply (same Vegvesen
cadences as before). One provider failing does not stop the others.

---

## Migration of secrets

Host migrate (`migrate-server.sh`) copies `data/secrets/` only when
`--components` includes `secrets`. Multi-provider files under
`/etc/navi-server/datex/` are **not** copied unless you use the deploy helper
or add them manually with `--with-secrets` style ops — never commit them.
