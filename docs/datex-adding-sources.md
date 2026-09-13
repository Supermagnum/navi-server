# Adding a DATEX source to navi-server

**Status:** operator / developer guide.  
The only implemented provider today is **NPRA** ([datex-npra.md](datex-npra.md)).
Open anonymous feeds are catalogued in [datex-open-feeds.md](datex-open-feeds.md);
they are **not** automatically polled. The shared framework under
`plugins/datex_common/` is ready when you add a plugin later.

Goal: keep **credentials and upstream polling on the server**; Navi clients only
`GET` cached files from the DocumentRoot (same model as packs).

---

## Public URL layout

| Path | Role |
|---|---|
| `/datex/<provider_id>/*.xml`, `/datex/<provider_id>/source.json` | Canonical per-provider cache |
| `/datex/providers.json` | Registry of known providers (ids, enabled/has_data hints; **no secrets**) |
| `/datex/GetSituation.xml` (and other legacy NPRA names + `source.json`) | **301** redirect to `/datex/npra/...` |

Do **not** add flat aliases for future providers unless explicitly required.

---

## Shared helpers (`plugins/datex_common/`)

| Module | Responsibility |
|---|---|
| `enable.py` | Truthy parse; `provider_enabled(id)` fail-closed |
| `secrets.py` | Load `NAV_DATEX_USERNAME` / `NAV_DATEX_PASSWORD` from env or secrets file |
| `publish.py` | Atomic write under `published/datex/<provider_id>/` |
| `providers_index.py` | Rebuild `published/datex/providers.json` |

Every provider: **off by default** via `NAVI_DATEX_<ID>_ENABLED`; credentials only
under `data/secrets/` mode `0600`.

---

## When you need a new source

| Situation | Approach |
|---|---|
| Another Basic-auth or API-key national node (like NPRA) | New optional plugin under `plugins/datex_<id>/` using `datex_common` |
| Anonymous open feed (France TIPI, Flanders, …) | Still a separate plugin — different URL layout / schema; do **not** overload NPRA config keys |
| Scaffold only | `scripts/new-datex-provider.sh <id>` creates a disabled stub (no live endpoints) |
| One-off manual mirror | Operator can `curl` into `data/published/datex/<id>/` for experiments; not production |

---

## Recommended plugin layout

```text
plugins/datex_<provider>/
  __init__.py
  __main__.py          # python -m plugins.datex_<provider>
  config.py            # enable via datex_common; publish_dir = published/datex/<id>/
  auth.py              # only if credentials required; wrap datex_common.secrets
  client.py            # fetch + atomic publish via datex_common.publish
  poll.py              # enable check FIRST, then creds, then network; refresh providers.json
  tests/
scripts/datex-<provider>-poll.sh
scripts/datex-<provider>.env.example
systemd/navi-datex-<provider>.{service,timer}
docs/datex-<provider>.md
```

Hard rules:

1. **Off by default** — master enable env flag; disabled path must not read secrets or open sockets.
2. **Secrets** only under `data/secrets/` mode `0600` (or env inject); never under DocumentRoot.
3. **Publish** unmodified upstream XML (plus `source.json`) under `data/published/datex/<provider_id>/`.
4. **Clients** never call the upstream DATEX node; they only GET this host.
5. **Logging** — no Authorization headers, no passwords, no full XML at INFO.
6. **Tests** — unit tests with fixtures; prove zero network when disabled.
7. Register the provider in `plugins/datex_common/providers_index.py` (`KNOWN_PROVIDERS`).

Wire-up checklist:

- [ ] `scripts/config.example.env` keys documented (`NAVI_DATEX_<ID>_ENABLED`, secrets path)
- [ ] `setup-server.sh` optional enable path (or explicit `--apply-datex-<provider>`)
- [ ] systemd units installed only when enabled
- [ ] README + docs table entry
- [ ] `docs/client-fetch.md` client URL section
- [ ] Uninstall removes only `published/datex/<id>/` (not the whole `datex/` tree)

---

## Config pattern

| Concern | Mechanism |
|---|---|
| Enable | `NAVI_DATEX_<ID>_ENABLED=0\|1` (NPRA: `NAVI_DATEX_NPRA_ENABLED`) |
| Secrets file | `NAVI_DATEX_<ID>_SECRETS_FILE` → typically `NAV_DATEX_USERNAME` / `NAV_DATEX_PASSWORD` (document aliases per plugin) |
| Endpoints | provider-specific |
| Poll | `scripts/datex-<id>-poll.sh` ← systemd timer |

For an **open** feed, omit secrets; still keep an enable flag and a documented
User-Agent / contact string where the publisher asks for one.

---

## Upstream differences to design for

Before coding, capture for the candidate feed:

- Auth model (none / Basic / API key / OAuth)
- DATEX version and payload shape (snapshot vs SOAP-wrapped events)
- Poll cadence and conditional GET support
- Licence / attribution text for `source.json`
- Whether filenames would collide with another provider (always use `/datex/<id>/`)

See also [datex-open-feeds.md](datex-open-feeds.md) for open candidates that are
**not** implemented yet.
