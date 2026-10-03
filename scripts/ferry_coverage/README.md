# Ferry coverage analysis

Read-only tools to inventory OSM ferries (Overpass), map them onto Navi regions,
compare published car packs, and rank **weekly** `NAVI_FERRY_LINKS_REGIONS`
candidates.

There is **no planet-wide bake**. Weekly (`data/regions.conf`) is the only
regular bake. Regions that need ferry boarding links and **fit** the weekly
budget should be added to the weekly list; over-budget / cut-off leaves get a
**targeted single-region bake now** (`./scripts/run-weekly.sh --region <id>`).

Do **not** commit raw Overpass JSON. Keep it under scratch outside the repo
(e.g. `/tmp/navi-ferry-coverage-YYYYMMDD`).

## Pipeline

```bash
SCRATCH=/tmp/navi-ferry-coverage-$(date -u +%Y%m%d)
CLONE=/tmp/navi-server-ferry-coverage
mkdir -p "$SCRATCH/logs"

python3 "$CLONE/scripts/ferry_coverage/overpass_fetch.py" --scratch "$SCRATCH"
python3 "$CLONE/scripts/ferry_coverage/tag_inventory.py" --scratch "$SCRATCH"
python3 "$CLONE/scripts/ferry_coverage/map_regions.py" --scratch "$SCRATCH"

export CARGO_TARGET_DIR="$SCRATCH/target"
cargo build -p pack-convert-core --release --bin ferry_pack_scan

# Check Monday bake timer; skip heavy pack scan if <3h away.
python3 "$CLONE/scripts/ferry_coverage/compare_packs.py" --scratch "$SCRATCH" --estimate-only

nohup nice -n 19 ionice -c3 \
  python3 "$CLONE/scripts/ferry_coverage/compare_packs.py" \
    --scratch "$SCRATCH" \
    --scan-bin "$SCRATCH/target/release/ferry_pack_scan" \
  > "$SCRATCH/logs/pack_compare.log" 2>&1 &

tail -f "$SCRATCH/logs/pack_scan_progress.log"

python3 "$CLONE/scripts/ferry_coverage/recommend_weekly.py" --scratch "$SCRATCH"
python3 "$CLONE/scripts/ferry_coverage/write_report.py" \
  --scratch "$SCRATCH" --repo "$CLONE"
```

Outputs: `docs/ferry-coverage.md` plus CSVs under `$SCRATCH/out/`.
Do not edit live `data/regions.conf` from these scripts.
