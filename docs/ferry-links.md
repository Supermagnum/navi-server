# Ferry terminal boarding links (car packs)

## Problem

Baked car packs keep `route=ferry` ways but drop the pier / footway / platform
links that connect a ferry endpoint to the road network. Terminals such as
Halhjem, Sandvikvag, Arsvagen and Mortavika (E39 south of Bergen) become islands
in the directed car graph, so Bergen to Stavanger is unroutable without a
client-side ferry overlay. Lavik-Oppedal still works when the ferry way shares a
node with a car highway.

## Step 1 findings (read-only)

### Where Pass 1 drops boarding ways

In `pack-convert-core/src/routing/graph/bbox_build.rs`:

- `keep_way_tag` retains routing tags (`highway`, `route`, `ferry`, `duration`,
  access, etc.). This change also keeps `man_made` (for piers) when ferry-link
  promotion needs it.
- Pass 1 / tile-assign keep a way only when `spill_keep_way` / the tile filter
  says so. **Without ferry links**, that is:

```text
highway_ok_for_profile (car: motorway…service/track)
  OR tags_indicate_ferry (route=ferry / ferry=*)
```

So `highway=footway|platform` and `man_made=pier` are dropped for the car
profile even when they are the only landside connection to a ferry.

### Vestlandet published pack audit (before this change)

Tool:

```bash
cargo run -p pack-convert-core --release --bin ferry_terminal_audit -- <pack_dir>
```

Target generation:

`data/published/packs/europe/norway/vestlandet/20261001T135715Z-625025-europe_norway_vestlandet-d5ab462f`

| Metric | Value |
|--------|-------|
| Pack dir size | **531 MiB** |
| Car graph tiles | **26** |
| Car graph size | **174 MiB** |
| Foot tiles | 26 |
| Convert wall (planet-leaves log) | **~94 s** (`convert_ms=94219`) |
| Peak RSS (same log) | **~4.1 GiB** |

Audit on that pack:

```text
car_edges=1066006 ferry_edges=487 disconnected_terminals=380
```

**68** distinct ferry names with at least one disconnected endpoint. Includes
the E39 Boknafjorden crossing **Arsvagen - Mortavika**. Also includes many
island/passenger-style names (Hurtigruten segments, local island hops).

Notable relative to the client finding:

- **Arsvagen - Mortavika**: disconnected (both ends appear in the audit).
- **Halhjem / Sandvikvag**: name does **not** appear in the disconnected list on
  this published pack (ferry edges for that name are already attached to the
  giant non-ferry car component, or the OSM name differs). Scratch re-bake with
  `--ferry-links` is still required to confirm Bergen→Stavanger end-to-end.
- **Lavik-Oppedal**: not in the disconnected list (expected: ferry touches highway).

Full unique disconnected names (published vestlandet):

Arasvika - Hennset; Arsvagen - Mortavika; Aukra - Hollingsholmen;
Brattvag - Dryna; Brattvag - Haroya; Byoyene; Daloy - Haldorsnes;
Dryna - Haroya; Eidssund - Halsnoy; Eidssund - Helgoy; Eidssund - Judaberg;
Eidssund - Nord-Hidle; Festoya - Solavagen; Finden - Findabotnen;
Finnoya - Sandoya; Fjortofta - Haroya; Forsand-Bratteli; Furneset - Molde;
Geiranger - Hellesylt; Geithus - Findabotnen; Geithus - Otterskred;
Halsa - Kanestraum; Hareid - Sulesund; Haugesund - Utsira; Hebnes - Foldoy;
Helgoy - Nord-Hidle; Hirtshals - Stavanger; Hjelmeland - Nesvik;
Hjelmeland - Skipavik; Hufthammar - Krokeide; Hurtigruten; Jelsa - Foldoy;
Judaberg - Fogn; Judaberg - Halsnoy; Judaberg - Helgoy; Judaberg - Nord-Hidle;
Krakhella - Rutledal; Kvamsoya - Voksa; Kvanne - Rykkjem; Kvitsoy - Mekjarvik;
Lauvik-Forsand; Lauvvik - Oanes; Linge - Eidsdal; Molde - Sekken;
Nedstrand - Nord-Hidle; Nesvik - Skipavik; Nordeide - Maren; Orta - Finnoya;
Orta - Sandoya; Os–Malkenes; Sandvika - Edoy; Sandoya - Ona;
Seivika - Tommervag; Smage - Orta; Solholmen - Mordalsvagen;
Stranda - Liabygda; Sykkylven - Magerholm; Solsnes - Afarnes; Sor-Bokn - Byre;
Trandal - Standal; Trandal - Saebo; Vetlesand - Ortnevik; Vetlesand - Sylvarnes;
Vik - Otterskred; Voksa - Aram; Vollevik - Finden; Vollevik - Sylvarnes.

(ASCII-folded above for this doc; the audit tool prints OSM names with full
Unicode.)

### Ferry costing today

`ferry_base_weight_m` (builder.rs) already:

- Uses OSM `duration` when parseable (`H:MM` / `HH:MM:SS`) and converts to
  drive-equivalent metres at `FERRY_DRIVE_EQUIV_KMH` (80).
- Otherwise scales geometric length by `80/10` (assumed 10 km/h ferry).
- Adds a **10 minute** car/truck boarding penalty.

`duration` is kept in Pass 1 tags and applied at bake into `base_weight`. It is
**not** stored as a separate on-disk edge field in the v9 pack (only
`edge_is_ferry` + weights). No format bump is required for duration retention.

`motor_vehicle` / `motorcar` gate car ferry admission via
`ferry_allowed_for_profile` (yes-set required for car/truck).

If a long unnamed ferry chain wins over a tagged shorter crossing, check for
missing/incorrect `duration` on the preferred OSM ways — costing is **not**
length-only when `duration` is present.

### Weekly re-bake decision

`scripts/run-weekly.sh` always:

1. Fetches (incremental preferred; 304 skip for unchanged PBFs).
2. Converts **every** region in `NAVI_REGIONS_CONF` (`convert-region.sh --all`).
3. Publishes.

There is **no** converter-hash / fingerprint gate that skips convert when the
binary changes. `NaviManifest::status_for_pbf` on the client side only checks
PBF size/mtime + format versions (we do **not** bump `graph_format_version`).

So deploying a converter that always promotes ferry links would change car
topology on the next Monday for every weekly region. That is why promotion is
**opt-in** (see below).

## Step 2 fix

When enabled for a region:

1. Pass 1 also spills boarding candidates (`man_made=pier`,
   `highway=footway|platform`) for car/truck profiles.
2. At car/truck graph build, BFS from car-capable ferry endpoints over candidate
   edges only, up to `FERRY_BOARDING_MAX_CHAIN_M` (**500 m**), and promote only
   ways on a path that reaches a car-drivable road node.
3. Passenger-only ferries (no `motor_vehicle`/`motorcar` yes) and their links
   stay non-car.
4. Manifest field `ferry_links_baked: true` on `{stem}.navi-manifest.json` and
   mirrored into published `manifest.json` when set.
5. Unknown manifest fields: `NaviManifest` / publish JSON use serde defaults
   without `deny_unknown_fields`. Adding `ferry_links_baked` is additive;
   older clients ignore it and keep their overlay when the flag is absent/false.

### Opt-in (prevents full Monday topology flip)

Default **off**.

| Env | Effect |
|-----|--------|
| `NAVI_BAKE_FERRY_LINKS=1` | Enable for every region in this convert |
| `NAVI_FERRY_LINKS_REGIONS=id1,id2,...` | Enable only listed bake ids |

`convert-region.sh` passes `--ferry-links` to `navi-indexed-convert` when either
matches. Leave both unset for Monday: weekly still converts, but car graphs keep
pre-change topology.

## Step 3 verify (vestlandet scratch)

**Blocked for the agent:** vestlandet PBF under
`data/scratch/extracts/` is not readable by the normal user after held-PBF
cleanup. Run the sudo fetch/copy commands below, then the user-side convert.

After bake:

```bash
export CARGO_TARGET_DIR=/tmp/navi-ferry-links-target
cargo run -p pack-convert-core --release --bin ferry_terminal_audit -- \
  /tmp/navi-ferry-scratch/europe_norway_vestlandet
```

Expect: `disconnected_terminals=0` for car-capable landings that have a pier
chain within 500 m, or each remainder explained (e.g. passenger-only, chain
longer than bound, missing OSM boarding geometry).

Directed reachability (no overlay) on the scratch pack:

- Bergen → Stavanger via Halhjem–Sandvikvag and Arsvagen–Mortavika
- Bergen → Forde via Lavik–Oppedal

Post-bake size / edges-added / peak memory: fill in after scratch convert.

### Per-region convert times (planet-leaves v9 log anchors)

| Region | convert_ms | ~minutes | peak RSS MiB |
|--------|------------|----------|--------------|
| europe_norway_vestlandet | 94219 | ~1.6 | 4115 |
| europe_norway_trondelag | 59691 | ~1.0 | 2300 |
| europe_norway_nord_norge | 119572 | ~2.0 | 3024 |
| europe_norway_sorlandet | 26185 | ~0.4 | 1302 |
| europe_norway_ostlandet | 190721 | ~3.2 | 7773 |

Estimated total for those five (convert only, this host class): **~8 minutes**
of convert wall time, plus fetch. Peak memory is dominated by ostlandet (~8 GiB);
weekly 16 GiB target remains OK if regions stay sequential.

## Publish vestlandet scratch (only after approval)

Do **not** run these until approved. One-line each:

```bash
# Install the branch convert binary where weekly/scripts resolve it (adjust if your resolve_convert_bin points elsewhere).
sudo install -o navit-server -g navit-server -m 755 \
  /tmp/navi-ferry-links-target/release/navi-indexed-convert \
  /media/navi/navi-server/target/release/navi-indexed-convert

# Copy scratch convert output into the server convert tree for publish-packs.
sudo -u navit-server rsync -a --delete \
  /tmp/navi-ferry-scratch/europe_norway_vestlandet/ \
  /media/navi/navi-server/data/scratch/convert/europe_norway_vestlandet/

# Publish only vestlandet (validate + HTTP packs swap for that region).
sudo -u navit-server env NAVI_PACK_CONFIG=/media/navi/navi-server/data/config.env \
  /media/navi/navi-server/scripts/publish-packs.sh --region europe_norway_vestlandet
```

## Scratch bake commands needing sudo

PBF for vestlandet is not readable by the normal user. Run these, then reply
with the output:

```bash
# Fetch vestlandet PBF as navit-server into extracts.
sudo -u navit-server env NAVI_PACK_CONFIG=/media/navi/navi-server/data/config.env \
  /media/navi/navi-server/scripts/fetch-extracts.sh europe_norway_vestlandet

# Make a user-readable copy for scratch convert (no write into published/).
sudo mkdir -p /tmp/navi-ferry-scratch && sudo chown "$USER:$USER" /tmp/navi-ferry-scratch
sudo cp -a /media/navi/navi-server/data/scratch/extracts/europe_norway_vestlandet-latest.osm.pbf \
  /tmp/navi-ferry-scratch/
sudo chown "$USER:$USER" /tmp/navi-ferry-scratch/europe_norway_vestlandet-latest.osm.pbf
```

Then as the normal user (from this branch clone):

```bash
cd /tmp/navi-server-ferry-links
export CARGO_TARGET_DIR=/tmp/navi-ferry-links-target
cargo build --release -p navi-indexed-convert
mkdir -p /tmp/navi-ferry-scratch/europe_norway_vestlandet
/usr/bin/time -v ./target/release/navi-indexed-convert \
  --data-dir /tmp/navi-ferry-scratch/europe_norway_vestlandet \
  --pbf /tmp/navi-ferry-scratch/europe_norway_vestlandet-latest.osm.pbf \
  --profiles car,foot \
  --elev-dir /media/navi/navi-server/data/elevation \
  --ferry-links
```

(If `./target/release/...` is missing because `CARGO_TARGET_DIR` is set, use
`/tmp/navi-ferry-links-target/release/navi-indexed-convert`.)

## CI checks run on this branch

- `cargo fmt --all -- --check`
- `cargo clippy --workspace --all-targets -- -D warnings`
- `cargo test --workspace` (includes `ferry_boarding_links_v9` fixture)

## Monday job confirmation

With `NAVI_BAKE_FERRY_LINKS` unset and `NAVI_FERRY_LINKS_REGIONS` empty (default),
the next `navi-pack-bake.timer` run will **not** bake ferry boarding links into
any region. It will still convert/publish as today; car topology for ferry
terminals stays as before until you name regions or set the global flag.
No `graph_format_version` bump; packs stay v9.
