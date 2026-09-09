# Adding a DATEX source to navi-server

**Status:** operator / developer guide.  
The only implemented provider today is **NPRA** ([datex-npra.md](datex-npra.md)).
Open anonymous feeds are catalogued in [datex-open-feeds.md](datex-open-feeds.md);
they are **not** automatically polled.

Goal: keep **credentials and upstream polling on the server**; Navi clients only
`GET` cached files from the DocumentRoot (same model as packs).

---

## When you need a new source

| Situation | Approach |
|---|---|
| Another Basic-auth or API-key national node (like NPRA) | New optional plugin under `plugins/`, mirrored after `plugins/datex_npra/` |
| Anonymous open feed (France TIPI, Flanders, …) | Still a separate plugin (or shared “open DATEX” plugin) — different URL layout / schema; do **not** overload NPRA config keys |
| One-off manual mirror | Operator can `curl` into `data/published/datex/` for experiments; not production — no timer, no `source.json` contract |

---

## Recommended plugin layout

Copy the NPRA plugin as a template:

```text
plugins/datex_<provider>/
  __init__.py
  __main__.py          # python -m plugins.datex_<provider>
  config.py            # enable flag, base URL, endpoints, intervals, UA
  auth.py              # only if credentials required; fail closed
  client.py            # fetch + atomic publish to data/published/datex/
  poll.py              # enable check FIRST, then creds, then network
  tests/
scripts/datex-<provider>-poll.sh
scripts/datex-<provider>.env.example
systemd/navi-datex-<provider>.{service,timer}
docs/datex-<provider>.md
```

Hard rules (match NPRA):

1. **Off by default** — master enable env flag; disabled path must not read secrets or open sockets.
2. **Secrets** only under `data/secrets/` mode `0600` (or env inject); never under DocumentRoot.
3. **Publish** unmodified upstream XML (plus a small `source.json` attribution file) under `data/published/datex/` (or a dedicated subpath if multiple providers would collide — decide before shipping).
4. **Clients** never call the upstream DATEX node; they only GET this host.
5. **Logging** — no Authorization headers, no passwords, no full XML at INFO.
6. **Tests** — unit tests with fixtures; prove zero network when disabled.

Wire-up checklist:

- [ ] `scripts/config.example.env` keys documented
- [ ] `setup-server.sh` optional enable path (or explicit `--apply-datex-<provider>`)
- [ ] systemd units installed only when enabled
- [ ] README + docs table entry
- [ ] `docs/client-fetch.md` client URL section if the public path differs
- [ ] Uninstall / purge script

---

## Config pattern (NPRA reference)

NPRA uses:

| Concern | Mechanism |
|---|---|
| Enable | `NAVI_DATEX_NPRA_ENABLED=0\|1` |
| Secrets file | `NAVI_DATEX_NPRA_SECRETS_FILE` → `NAV_DATEX_USERNAME` / `NAV_DATEX_PASSWORD` |
| Endpoints | comma list + optional per-endpoint poll intervals |
| Poll | `scripts/datex-npra-poll.sh` ← `navi-datex-npra.timer` |

For an **open** feed, omit secrets; still keep an enable flag and a documented
User-Agent / contact string where the publisher asks for one.

---

## Upstream differences to design for

Before coding, capture for the candidate feed:

1. Auth: none / Basic / Bearer / mutual TLS
2. Pull shape: single snapshot URL vs directory of many files vs SOAP
3. DATEX version: v2 / v2.3 / v3 (+ `MessageContainer` or not)
4. Conditional GET: `ETag` / `Last-Modified` / neither
5. Cadence and max body size
6. Licence and required attribution text for `source.json`
7. Whether multiple situations arrive as one document or thousands of small files (France TIPI)

Open-feed examples and URLs: [datex-open-feeds.md](datex-open-feeds.md).

---

## Client contract

Until a second provider exists, clients should keep using the NPRA paths in
[datex-npra.md — How clients fetch DATEX data](datex-npra.md#how-clients-fetch-datex-data).
If a new provider publishes under a different URL prefix, document it in
`client-fetch.md` and bump any client capability notes — do not silently reuse
`/datex/GetSituation.xml` for a different country’s schema without a versioned
path or manifest.
