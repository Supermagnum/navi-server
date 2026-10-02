# Ferry terminal boarding links (car packs)

## Problem

Baked car packs keep `route=ferry` ways but historically drop pier / footway /
platform links that connect a ferry endpoint to the road network. Where that
happens, the landing is a directed-graph island and the client must attach a
ferry overlay. Some landings (e.g. Lavik-Oppedal) already share a node with a
car highway and need no overlay.

## Step 1 findings (read-only)

### Where Pass 1 drops boarding ways

In `pack-convert-core/src/routing/graph/bbox_build.rs`:

- `keep_way_tag` retains routing tags (`highway`, `route`, `ferry`, `duration`,
  access, etc.). This change also keeps `man_made` (for piers).
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
| Convert wall (planet-leaves log) | **~94 s** (`convert_ms=94219`) |
| Peak RSS (same log) | **~4.1 GiB** |

Naive “not in giant non-ferry component” count was **380** endpoint hits / **68**
named ferries. That over-counts: landings on large secondary road networks
(Stavanger-side Boknafjorden, island networks) are joined to the mainland only
by ferry, so they are not in the single giant component even when boarding is
fine.

Refined classification on the same published pack:

| Class | Count | Meaning |
|-------|------:|---------|
| `no_road` | 63 | ferry endpoint with zero non-ferry incident edges |
| `tiny` (&lt;50 nodes) | 11 | tiny non-ferry component (likely missing boarding) |
| `other_comp` | (large) | on a big road component that is not the giant — expected |

E39 terminals:

- **Arsvågen**: already has `highway=service` (movable bridge) into the ferry
  node in both published and scratch packs — not a pier-gap.
- **Mortavika / Halhjem / Sandvikvåg / Lavik / Oppedal**: on the giant or
  otherwise road-connected; Halhjem–Sandvikvåg ferry edges present.

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

### Weekly re-bake decision

`scripts/run-weekly.sh` always:

1. Fetches (incremental preferred; 304 skip for unchanged PBFs).
2. Converts **every** region in `NAVI_REGIONS_CONF` (`convert-region.sh --all`).
3. Publishes.

There is **no** converter-hash / fingerprint gate that skips convert when the
binary changes. So a converter that always promotes ferry links would change
car topology on the next Monday for every weekly region. Promotion is **opt-in**
(see below). No `graph_format_version` bump (packs stay v9).

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

Scratch bake (normal user, after sudo PBF copy):

```text
convert_ms ≈ 79270 (wall ~1:20)
peak_rss_mb ≈ 3406 (time -v Max RSS ≈ 3506540 kB ≈ 3.3 GiB)
ferry_links_baked: true
car_edges: 1066006 → 1066022 (+16)
car tiles: 26 → 26
car graph size: published 174 MiB → scratch 410 MiB
pack dir: published 531 MiB → scratch 1.2 GiB
```

Scratch includes wetland/poi/barrier and a fuller elevation encode; tile count is
unchanged. The +16 car edges are the promoted boarding chains (bounded).

Refined audit after `--ferry-links`:

| Class | Published | Scratch |
|-------|----------:|--------:|
| `no_road` | 63 | **58** (−5) |
| `tiny` (&lt;50) | 11 | 14 |

Remaining `no_road` / `tiny` are mostly passenger/Hurtigruten landings, foreign
terminals (Hirtshals), or OSM geometry without a ≤500 m pier/footway/platform
chain to a car road — each is expected to stay non-car or keep needing overlay.

### Directed routes (no client overlay)

On the scratch pack:

| Route | Result |
|-------|--------|
| Bergen → Stavanger (station 58.9670,5.7315) | **OK ~205 km** via **Halhjem–Sandvikvåg** and **Arsvågen–Mortavika** |
| Bergen → Førde | **OK ~170 km** via **Lavik–Oppedal** |

Note: city-centre snap `(58.9700, 5.7331)` hits OSM node `11335393456`, a
directed dead-end (indeg=outdeg=1) that is undirected-connected but not
directed-reachable. That is a snap/one-way issue, not a missing ferry link.
The same Bergen→StavangerStation route also succeeds on the **published** pack
without this change — E39 landings already touch `highway=service` / trunk.

### CI

- `cargo fmt --all -- --check`
- `cargo clippy --workspace --all-targets -- -D warnings`
- `cargo test --workspace` (includes `ferry_boarding_links_v9` fixture)

### Per-region convert times (planet-leaves v9 log anchors)

| Region | convert_ms | ~minutes | peak RSS MiB |
|--------|------------|----------|--------------|
| europe_norway_vestlandet | 94219 | ~1.6 | 4115 |
| europe_norway_trondelag | 59691 | ~1.0 | 2300 |
| europe_norway_nord_norge | 119572 | ~2.0 | 3024 |
| europe_norway_sorlandet | 26185 | ~0.4 | 1302 |
| europe_norway_ostlandet | 190721 | ~3.2 | 7773 |

Estimated total for those five (convert only): **~8 minutes**, plus fetch.
Peak memory dominated by ostlandet (~8 GiB); weekly 16 GiB target OK if sequential.

Coastal regions that benefit most from opt-in re-bake: the five Norway rows
above (vestlandet already scratch-verified).

## Publish vestlandet scratch (only after approval)

Do **not** run these until approved. One-line each:

```bash
# Install the branch convert binary where weekly/scripts resolve it.
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

To bake other Norway coastal regions later without a full world run:

```bash
sudo -u navit-server env NAVI_PACK_CONFIG=/media/navi/navi-server/data/config.env \
  NAVI_FERRY_LINKS_REGIONS=europe_norway_vestlandet,europe_norway_trondelag,europe_norway_nord_norge,europe_norway_sorlandet,europe_norway_ostlandet \
  /media/navi/navi-server/scripts/convert-region.sh --region <one-id>
```

## Scratch bake commands that needed sudo (already run)

```bash
sudo -u navit-server env NAVI_PACK_CONFIG=/media/navi/navi-server/data/config.env \
  /media/navi/navi-server/scripts/fetch-extracts.sh europe_norway_vestlandet
sudo mkdir -p /tmp/navi-ferry-scratch && sudo chown "$USER:$USER" /tmp/navi-ferry-scratch
sudo cp -a /media/navi/navi-server/data/scratch/extracts/europe_norway_vestlandet-latest.osm.pbf \
  /tmp/navi-ferry-scratch/
sudo chown "$USER:$USER" /tmp/navi-ferry-scratch/europe_norway_vestlandet-latest.osm.pbf
```

User-side convert used:

```bash
cd /tmp/navi-server-ferry-links
export CARGO_TARGET_DIR=/tmp/navi-ferry-links-target
/usr/bin/time -v /tmp/navi-ferry-links-target/release/navi-indexed-convert \
  --data-dir /tmp/navi-ferry-scratch/europe_norway_vestlandet \
  --pbf /tmp/navi-ferry-scratch/europe_norway_vestlandet-latest.osm.pbf \
  --profiles car,foot \
  --elev-dir /media/navi/navi-server/data/elevation \
  --ferry-links
```

## Monday job confirmation

With `NAVI_BAKE_FERRY_LINKS` unset and `NAVI_FERRY_LINKS_REGIONS` empty (default),
the next `navi-pack-bake.timer` run will **not** bake ferry boarding links into
any region. It will still convert/publish as today; car topology for ferry
terminals stays as before until you name regions or set the global flag.
