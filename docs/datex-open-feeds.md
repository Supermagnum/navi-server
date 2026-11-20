# Open DATEX II feeds (no registration)

**Status:** research / operator reference (re-probed 2026-11-19; wired 2026-11-20).  
Upstream sources that answered anonymous HTTP GET during the survey. Licences
and URLs can change — re-check before production use.

**Implemented** as multi-provider TOML under `config/datex/providers/` (auth
`none`), polled by `scripts/datex-poll-all.sh` / `navi-datex-npra.timer`:

| Provider id | publish_id | Notes |
|---|---|---|
| `be-flanders` | `flanders` | DATEX v3 full snapshot |
| `fi-digitraffic` | `digitraffic` | gzip Accept-Encoding |
| `lu-cita` | `cita` | `www.cita.lu/.../situationrecord` (empty `situationrecord36` skipped) |
| `nl-ndw` | `ndw` | gzip bodies; skips ~188 MiB planningsfeed |
| `fr-tipi` | `tipi` | QTV + Evenementiel directory aggregate (newest 50) |
| `bg-ria` | `bulgaria` | `{YYYYMMDD}` URLs, `Europe/Sofia` |
| `pt-mobie` | `mobie` | EV DATEX v3.3; large payloads / slow cadence |

NPRA stays separate (`no-vegvesen` / `/datex/npra/`, Basic auth, fail-closed).
See [datex-providers.md](datex-providers.md).

Live apply (copies open tomls + shared fetcher; **does not** touch NPRA secrets
or MWM):

```bash
sudo /media/navi/haaken-scratch/navi-server-mwm/scripts/apply-datex-open-feeds.sh
```

Primary external catalogues used for discovery (many more countries, mixed
formats, mixed access rules):

- [graphhopper/open-traffic-collection](https://github.com/graphhopper/open-traffic-collection)
  — community list of traffic open-data portals. Links below marked **(GH)**
  come from that README; status was re-checked with anonymous HTTP GET on
  2026-11-19. **Where GH notes registration (or a probe shows a portal-only
  page), registration is still required** even if the landing URL returns 200.
- [traff / roadeagle Data Sources](https://gitlab.com/traffxml/roadeagle/-/wikis/Data-Sources)
  — additional no-registration claims (Bulgaria, regional DE MDM mirrors).
- [DATEX@NAPs distributions](https://datex2.naps.inqms.tamtamresearch.com/naps-browser/index/distribution/index.html)
  — auth metadata often listed.

Most European National Access Points (NAPs) still require registration or an
API key even when the data is free. Fully open, walk-up DATEX situation feeds
are uncommon.

Related navi-server docs:

- [DATEX NPRA redistribution](datex-npra.md) — implemented optional plugin (Basic auth)
- [DATEX multi-provider setup](datex-providers.md) — preferred way to add a poller
- [Adding a DATEX source](datex-adding-sources.md) — legacy plugin stub notes
- [Client fetch](client-fetch.md#datex-npra-optional) — what clients GET from this host

---

## Probe method (2026-11-19)

Anonymous `curl` GET with `Accept: application/xml, text/xml, */*` and a
plain survey User-Agent. Digitraffic additionally requires
`Accept-Encoding: gzip` (HTTP 406 without it). Gzip bodies (NDW `*.xml.gz`)
were decompressed before structure checks. Pass means HTTP 2xx, non-trivial
body, and DATEX-like markers (`d2LogicalModel`, `messageContainer`,
`SituationPublication`, `payloadPublication`, `MeasuredDataPublication`,
or `EnergyInfrastructure`).

---

## Confirmed open DATEX-style feeds (no login / no registration)

Live-probed with anonymous GET on **2026-11-19**. These returned DATEX /
DATEX-like XML without credentials.

| Country | Operator / content | Working endpoint(s) | Data types | Notes | Last-tested |
|---|---|---|---|---|---|
| **France** | TIPI / Bison Futé — DIR events on non-concession RRN | Directory: [Evenementiel-DIR/grt/RRN/](https://tipi.bison-fute.gouv.fr/bison-fute-ouvert/publicationsDIR/Evenementiel-DIR/grt/RRN/) (thousands of numbered `*.xml`); sample file returned SOAP-wrapped DATEX II **v2** `SituationPublication`. Also open: [QTV-DIR/qtvDir.xml](https://tipi.bison-fute.gouv.fr/bison-fute-ouvert/publicationsDIR/QTV-DIR/qtvDir.xml) (`MeasuredDataPublication`). | Events (per-file), flow/speed (QTV) | Opendata-style licence. **Action B / Action C need credentials.** Dataset pages on [transport.data.gouv.fr events](https://transport.data.gouv.fr/datasets/evenements-routiers-sur-le-reseau-routier-national-non-concede) and [traffic state](https://transport.data.gouv.fr/datasets/etat-de-circulation-en-temps-reel-sur-le-reseau-national-routier-non-concede) **(GH)**. Directory listing alone is HTML — fetch individual `*.xml`. | 2026-11-19 **PASS** |
| **Belgium (Flanders)** | Verkeerscentrum — highways + regional incidents / works | `https://www.verkeerscentrum.be/uitwisseling/datex2v3full` (v3) and `…/datex2full` (v2) **(GH)** | Situation / works | Anonymous DATEX XML (~280–480 KiB). CC-BY / Modellicentie. GH notes Alert-C location quirks for Belgium. | 2026-11-19 **PASS** |
| **Finland** | Digitraffic — traffic messages | `https://tie.digitraffic.fi/api/traffic-message/v1/messages.datex2?inactiveHours=0&situationType=TRAFFIC_ANNOUNCEMENT` (also `ROAD_WORK`, `WEIGHT_RESTRICTION`, …) | Traffic announcements, roadworks, weight restrictions | DATEX II **v2.3**. No auth. **Requires `Accept-Encoding: gzip`.** Portal **(GH)**: [digitraffic.fi road traffic](https://www.digitraffic.fi/en/road-traffic/). Older `…/api/v1/data/*-datex2` paths returned **404**. Vayla static `aineistot.vayla.fi/..._d2.xml` **(GH)** still **HTTP 400** — prefer Digitraffic API. | 2026-11-19 **PASS** |
| **Luxembourg** | CITA traffic events | `https://cita.lu/info_trafic/datex/situationrecord36` and `https://www.cita.lu/info_trafic/datex/situationrecord` **(GH)** | Situation records | DATEX II situation XML; **CC0** via [data.public.lu](https://data.public.lu/en/datasets/cita-evenements-trafic-en-datex-ii-v3-6/). | 2026-11-19 **PASS** |
| **Netherlands** | NDW open data | Index: [opendata.ndw.nu](https://opendata.ndw.nu/) **(GH)**. Working examples: `veiligheidsgerelateerde_informatie_srti.xml.gz`, `planningsfeed_wegwerkzaamheden_en_evenementen.xml.gz`, `tijdelijke_verkeersmaatregelen_afsluitingen.xml.gz`, `tijdelijke_verkeersmaatregelen_maximum_snelheden.xml.gz`, `actueel_beeld.xml.gz` | SRTI, works/events planning, closures, speed measures, traffic image | Anonymous GET; bodies are gzip DATEX II (often **v3** `messageContainer`). **Filename change:** older `veiligheidsgerelateerde_berichten_srti.xml.gz` returned **404** on 2026-11-19 — use `…_informatie_srti…`. Short names in older GH notes (`srti.xml.gz`, …) still 404. AVG measurement files migrate toward v3 names by 2027-02-09 (see portal notice). | 2026-11-19 **PASS** |
| **Bulgaria** | Road Infrastructure Agency (RIA) via LIMA Datasheet | Pattern: `https://datasheet.api.bg/files/YYYYMMDD_<group>_<code>.xml` (date in Europe/Sofia). Confirmed: `…_danger_d01.xml`, `…_danger_d02.xml`, `…_roadworks_r01.xml`, `…_roadworks_r02.xml`, `…_roadworks_r03.xml`, `…_other_o04.xml`, `…_other_o05.xml`, `…_other_o06.xml`. Portal: [datasheet.api.bg](https://datasheet.api.bg/?lang=en) | Danger, roadworks, other situation publications | DATEX II **v2** `SituationPublication`. No registration (DATEX@NAPs: `subscription_needed: no`). URL date rolls around midnight EET; empty/missing kinds return HTML 404. Portal HTML `?download=1` is **not** a raw XML feed. Licence: not declared on NAP metadata. | 2026-11-19 **PASS** (listed codes) |
| **Portugal** | MOBI.E / NAP — EV charging (AFIR) | `https://pgm.mobie.pt/integration/nap/evChargingInfra` (static inventory) and `https://pgm.mobie.pt/integration/nap/evActualStatus` (dynamic status) | Energy infrastructure table / status (DATEX II **v3.3**) | Anonymous GET; large XML (multi‑MB / tens of MB). NAP Portugal cards describe **livre acesso**. Not road-situation SRTI — useful if clients need charging DATEX. | 2026-11-19 **PASS** |

### France TIPI open tree (detail)

Base: `https://tipi.bison-fute.gouv.fr/bison-fute-ouvert/publicationsDIR/`

| Path | Role | Probe 2026-11-19 |
|---|---|---|
| `Evenementiel-DIR/grt/RRN/` | Event publications for the Réseau Routier National (many numbered `*.xml`) | Directory HTML; sample `*.xml` **PASS** (SOAP + DATEX v2) |
| `Evenementiel-DIR/cnir/` | Related event publications (CNIR) | Not re-listed file-by-file this run |
| `QTV-DIR/qtvDir.xml` | Flow / speed style traffic data | **PASS** (`MeasuredDataPublication`) |
| `TP-DIR/` | Travel-time style data | Not re-fetched this run |
| `TRAFICOLOR-DIR/` | Urban / regional traffic-colour feeds | Not re-fetched this run |

Official framing: [Données sur le RRN](https://www.bison-fute.gouv.fr/donnees-sur-la-circulation-du,langen.html).

### Bulgaria URL construction

```text
https://datasheet.api.bg/files/YYYYMMDD_<group>_<code>.xml
```

Use today's date in `Europe/Sofia`. Catalogue links on the portal use
`?lang=en&g=<group>&c=<code>` (HTML UI only). Prefer the `/files/…xml`
pattern for machine pull.

---

## Probe failures / do not treat as open pull feeds (2026-11-19)

| Candidate | Result | Disposition |
|---|---|---|
| NDW `veiligheidsgerelateerde_berichten_srti.xml.gz` | HTTP **404** | Renamed — use `veiligheidsgerelateerde_informatie_srti.xml.gz` (PASS) |
| Digitraffic without `Accept-Encoding: gzip` | HTTP **406** | Still open; client must send gzip Accept-Encoding |
| Digitraffic legacy `…/api/v1/data/traffic-disorders-datex2` (and roadworks twin) | HTTP **404** | Prefer `traffic-message/v1/messages.datex2?situationType=…` |
| Finland Vayla `aineistot.vayla.fi/roadworks/roadworks_d2.xml` | HTTP **400** | Dead for pull; use Digitraffic |
| Finland Vayla weight `…/painorajoitukset_d2.xml` | HTTP **400** | Dead for pull; use Digitraffic |
| Germany NRW MDM mirrors (`open.nrw/broker.mdm-portal.de/…`) | HTTP **503** | Claimed no-reg historically; **unavailable** this probe |
| Germany Bavaria `data-run.info/BASt-MDM-Interface/…` | Timeout / no response | **FAIL** this probe |
| Portugal Porto municipal DATEX URL (opendata.porto.digital → dadosabertos.cm-porto.pt) | DNS failure | **FAIL** this probe — not listed as confirmed |
| Spain DGT EV `infocar.dgt.es/.../electrolineras.xml` | HTTP **502** HTML | **FAIL** this probe |
| TIPI directory URL alone (no specific `*.xml`) | HTTP 200 HTML listing | Not a feed body; fetch child XML files |

---

## From GraphHopper collection — portals that work but need registration

These **(GH)** landing pages may respond, but **DATEX pull access
requires registration / contract / API credentials** (per GH and/or operator
docs). Do not treat a 200 HTML portal as an open feed.

| Country | Working portal / info URL | Registration |
|---|---|---|
| **Austria (ASFINAG)** | [contentportal.asfinag.at/data](https://contentportal.asfinag.at/data) **(GH)**; open Atom/RSS traffic messages: [publiccontent.asfinag.at/rss/…](https://publiccontent.asfinag.at/rss/feed/de/trafficmessages) **(GH)** (RSS, not full DATEX) | **Registration required** for DATEX packages; some free, some fee **(GH)** |
| **Czechia** | [registr.dopravniinfo.cz](https://registr.dopravniinfo.cz/en/) **(GH)** | **Registration required** (free); push to your server **(GH)** |
| **Estonia** | [tarktee.transpordiamet.ee](https://tarktee.transpordiamet.ee/) (old `tarktee.mnt.ee` redirects here) **(GH)** | **Registration required**, free of charge **(GH)** |
| **Norway (NPRA)** | [What is DATEX](https://www.vegvesen.no/en/fag/technology/open-data/a-selection-of-open-data/what-is-datex/) | **Registration + Basic auth** — see [datex-npra.md](datex-npra.md). Older GH path under `…/apne-data/Datex/publikasjoner` returned 404. |
| **Poland (NAP)** | [kpd.gddkia.gov.pl](https://kpd.gddkia.gov.pl/) **(GH)** | **Registration required** for DATEX **(GH)** |
| **Slovenia** | [promet.si plugins for developers](https://www.promet.si/en/plugins-for-developers) (old `…/etd.aspx` redirects) **(GH)** | **Registration required** **(GH)** |
| **Germany (Mobilithek)** | [mobilithek.info](https://mobilithek.info/) **(GH)** | Portal account / licence typical for DATEX distributions |
| **Germany (unofficial motorway API)** | [autobahn.api.bund.dev](https://autobahn.api.bund.dev/) **(GH)** | Not classic DATEX NAP; check current API terms |
| **Latvia** | [transportdata.gov.lv](https://transportdata.gov.lv/en) **(GH)** | Free account + per-dataset subscribe **(GH)** — not walk-up anonymous |

---

## From GraphHopper collection — DATEX entries with dead or outdated URLs

Re-probe before relying on these **(GH)** entries:

| Entry | Probe |
|---|---|
| Finland Vayla `https://aineistot.vayla.fi/roadworks/roadworks_d2.xml` | HTTP 400 (2026-09-09 and 2026-11-19) |
| Finland Vayla weight `…/painorajoitukset_d2.xml` | HTTP 400 (2026-09-09 and 2026-11-19) |
| NL short names `srti.xml.gz`, `incidents.xml.gz`, `wegwerkzaamheden.xml.gz`, `gebeurtenisinfo.xml.gz` | HTTP 404 (index still lists longer filenames) |
| NL `veiligheidsgerelateerde_berichten_srti.xml.gz` | HTTP 404 (2026-11-19); replaced by `…_informatie_srti…` |
| Sweden Trafikverket open-data URL in GH README | HTTP 404 (2026-09-09) |
| UK England `trafficengland.com/services-info` | HTTP 404 (2026-09-09) |
| UK Scotland `trafficscotland.org/datex/` | HTTP 404 (2026-09-09) |

GH still states for England/Scotland/Sweden DATEX: **registration required** when those services are alive.

---

## Related non-DATEX open traffic XML (GH)

Useful for context, not DATEX II situation plugins:

| Source | URL | Notes | Last-tested |
|---|---|---|---|
| Catalonia incidents | `http://www.gencat.cat/transit/opendata/incidenciesGML.xml` **(GH)** | Custom GML/WFS-style XML; anonymous 200 (~199 KiB) | 2026-11-19 **PASS** (non-DATEX) |

---

## Needs registration / API key / contract (summary)

| Country / feed | Access |
|---|---|
| **Norway (NPRA)** | Basic auth after register — see [datex-npra.md](datex-npra.md) |
| **Switzerland** | API key / Bearer (opentransportdata.swiss) |
| **France Action B / Action C** | Login on TIPI restricted paths |
| **Austria ASFINAG DATEX**, **Czechia**, **Estonia**, **Poland NAP**, **Slovenia**, **Latvia**, **UK** (when online), **Sweden Trafikverket** | Registration per GH / operator |
| **Denmark, often Germany Mobilithek** | Portal account and/or licence even when free of charge |

Standards / background: [datex2.eu](https://datex2.eu/) **(GH)**.

---

## Practical differences vs NPRA

| Aspect | NPRA (navi-server plugin) | Typical open feeds above |
|---|---|---|
| Auth | Basic auth + account | None (open) **or** registration (GH portals) |
| Packaging | Few large snapshot endpoints | Directory of small files **or** one snapshot URL **or** gzip API **or** dated daily files |
| Schema | DATEX II **v3.1** `MessageContainer` | Mix of **v2** / **v2.3** / **v3** / EV EnergyInfrastructure |
| Redistribution | Cached under `/datex/npra/` when enabled | Cached under `/datex/<publish_id>/` via provider TOMLs |

Do not assume an open feed can be dropped into the NPRA poller unchanged — URL shape, auth, schema version, and update cadence differ. Open feeds use [datex-providers.md](datex-providers.md) (`auth_type = "none"`).

When in doubt: if GraphHopper or the operator says **registration required**, treat it as credentialed even if a marketing page is publicly reachable.
