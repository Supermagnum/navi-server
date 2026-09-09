# Open DATEX II feeds (no registration)

**Status:** research / operator reference (surveyed 2026-09-09).  
**Not** a navi-server poller plugin. These are upstream sources that answered
anonymous HTTP GET during the survey. Licences and URLs can change — re-check
before production use.

Primary external catalogue used for discovery (many more countries, mixed
formats, mixed access rules):

- [graphhopper/open-traffic-collection](https://github.com/graphhopper/open-traffic-collection)
  — community list of traffic open-data portals. Links below marked **(GH)**
  come from that README; status was re-checked with anonymous HTTP GET on
  2026-09-09. **Where GH notes registration (or a probe shows a portal-only
  page), registration is still required** even if the landing URL returns 200.

Most European National Access Points (NAPs) still require registration or an
API key even when the data is free. Fully open, walk-up DATEX situation feeds
are uncommon. Another discovery catalogue (auth metadata often listed):
[DATEX@NAPs distributions](https://datex2.naps.inqms.tamtamresearch.com/naps-browser/index/distribution/index.html).

Related navi-server docs:

- [DATEX NPRA redistribution](datex-npra.md) — implemented optional plugin (Basic auth)
- [Adding a DATEX source](datex-adding-sources.md) — how to wire a new poller
- [Client fetch](client-fetch.md#datex-npra-optional) — what clients GET from this host

---

## Confirmed open DATEX-style feeds (no login / no registration)

Live-probed with anonymous GET on 2026-09-09. These returned DATEX / DATEX-like
XML without credentials.

| Country | Content | Working endpoint | Notes |
|---|---|---|---|
| **France** | DIR events on non-concession national network (RRN) | [TIPI Evenementiel-DIR](https://tipi.bison-fute.gouv.fr/bison-fute-ouvert/publicationsDIR/Evenementiel-DIR/) (e.g. `grt/RRN/`) | DATEX II **v2**, SOAP-wrapped **per-event** XML. Opendata-style licence. Also open: `QTV-DIR/`, `TP-DIR/`, `TRAFICOLOR-DIR/`. **Action B / Action C need credentials.** Dataset pages on [transport.data.gouv.fr events](https://transport.data.gouv.fr/datasets/evenements-routiers-sur-le-reseau-routier-national-non-concede) and [traffic state](https://transport.data.gouv.fr/datasets/etat-de-circulation-en-temps-reel-sur-le-reseau-national-routier-non-concede) **(GH)**. |
| **Belgium (Flanders)** | Highways + some regional incidents / works | `https://www.verkeerscentrum.be/uitwisseling/datex2v3full` (v3) and `…/datex2full` (v2) **(GH)** | Anonymous DATEX XML. CC-BY. GH notes Alert-C location quirks for Belgium. |
| **Finland** | Digitraffic traffic messages | `https://tie.digitraffic.fi/api/traffic-message/v1/messages.datex2?inactiveHours=0&situationType=TRAFFIC_ANNOUNCEMENT` (also `ROAD_WORK`, `WEIGHT_RESTRICTION`, …) | DATEX II **v2.3**. No auth. Requires `Accept-Encoding: gzip`. Portal **(GH)**: [digitraffic.fi road traffic](https://www.digitraffic.fi/en/road-traffic/). Older Vayla static paths `aineistot.vayla.fi/..._d2.xml` **(GH)** returned HTTP 400 when probed — prefer Digitraffic API. |
| **Luxembourg** | CITA traffic events | `https://cita.lu/info_trafic/datex/situationrecord36` and `https://www.cita.lu/info_trafic/datex/situationrecord` **(GH)** | DATEX II situation XML; **CC0** via [data.public.lu](https://data.public.lu/en/datasets/cita-evenements-trafic-en-datex-ii-v3-6/). |
| **Netherlands** | NDW open data (SRTI, works, closures, measurements, …) | Index: [opendata.ndw.nu](https://opendata.ndw.nu/) **(GH)** | Anonymous GET. Working examples (2026-09-09): `veiligheidsgerelateerde_berichten_srti.xml.gz`, `planningsfeed_wegwerkzaamheden_en_evenementen.xml.gz`, `tijdelijke_verkeersmaatregelen_afsluitingen.xml.gz`, `actueel_beeld.xml.gz`, … Short names in older GH notes (`srti.xml.gz`, `incidents.xml.gz`, `wegwerkzaamheden.xml.gz`, `gebeurtenisinfo.xml.gz`) returned **404** — use the current index filenames. |

### France TIPI open tree (detail)

Base: `https://tipi.bison-fute.gouv.fr/bison-fute-ouvert/publicationsDIR/`

| Path | Role |
|---|---|
| `Evenementiel-DIR/grt/RRN/` | Event publications for the Réseau Routier National (many numbered `*.xml`) |
| `Evenementiel-DIR/cnir/` | Related event publications (CNIR) |
| `QTV-DIR/` | Flow / speed style traffic data (`qtvDir.xml`, …) |
| `TP-DIR/` | Travel-time style data |
| `TRAFICOLOR-DIR/` | Urban / regional traffic-colour feeds |

Official framing: [Données sur le RRN](https://www.bison-fute.gouv.fr/donnees-sur-la-circulation-du,langen.html).

---

## From GraphHopper collection — portals that work but need registration

These **(GH)** landing pages responded in the survey, but **DATEX pull access
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

---

## From GraphHopper collection — DATEX entries with dead or outdated URLs

Re-probe before relying on these **(GH)** entries:

| Entry | Probe (2026-09-09) |
|---|---|
| Finland Vayla `https://aineistot.vayla.fi/roadworks/roadworks_d2.xml` | HTTP 400 |
| Finland Vayla weight `…/painorajoitukset_d2.xml` | HTTP 400 |
| NL short names `srti.xml.gz`, `incidents.xml.gz`, `wegwerkzaamheden.xml.gz`, `gebeurtenisinfo.xml.gz` | HTTP 404 (index still lists longer filenames) |
| Sweden Trafikverket open-data URL in GH README | HTTP 404 |
| UK England `trafficengland.com/services-info` | HTTP 404 |
| UK Scotland `trafficscotland.org/datex/` | HTTP 404 |

GH still states for England/Scotland/Sweden DATEX: **registration required** when those services are alive.

---

## Related non-DATEX open traffic XML (GH)

Useful for context, not DATEX II situation plugins:

| Source | URL | Notes |
|---|---|---|
| Catalonia incidents | `http://www.gencat.cat/transit/opendata/incidenciesGML.xml` **(GH)** | Custom GML/WFS-style XML; anonymous 200 |

---

## Needs registration / API key / contract (summary)

| Country / feed | Access |
|---|---|
| **Norway (NPRA)** | Basic auth after register — see [datex-npra.md](datex-npra.md) |
| **Switzerland** | API key / Bearer (opentransportdata.swiss) |
| **France Action B / Action C** | Login on TIPI restricted paths |
| **Austria ASFINAG DATEX**, **Czechia**, **Estonia**, **Poland NAP**, **Slovenia**, **UK** (when online), **Sweden Trafikverket** | Registration per GH / operator |
| **Denmark, often Germany Mobilithek** | Portal account and/or licence even when free of charge |

Standards / background: [datex2.eu](https://datex2.eu/) **(GH)**.

---

## Practical differences vs NPRA

| Aspect | NPRA (navi-server plugin) | Typical open feeds above |
|---|---|---|
| Auth | Basic auth + account | None (open) **or** registration (GH portals) |
| Packaging | Few large snapshot endpoints | Directory of small files **or** one snapshot URL **or** gzip API |
| Schema | DATEX II **v3.1** `MessageContainer` | Mix of **v2** / **v2.3** / **v3** |
| Redistribution | Cached under `/datex/` when enabled | Not implemented in navi-server today |

Do not assume an open feed can be dropped into the NPRA poller unchanged — URL shape, auth, schema version, and update cadence differ. Follow [Adding a DATEX source](datex-adding-sources.md) for a new provider plugin.

When in doubt: if GraphHopper or the operator says **registration required**, treat it as credentialed even if a marketing page is publicly reachable.
