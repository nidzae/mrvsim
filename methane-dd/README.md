# methane-dd — supply-chain methane due diligence

Turns an area of interest along a gas supply chain into a defensible methane intensity with error bounds, from every public plume detection **and every observation that saw nothing** (spec: `docs/SPEC.md`). It shares sensor detection models with MRVSim (the OSSE in the parent folder) and runs them retrospectively on real observation records.

Status (2026-10-09): command-line v1 on Carbon Mapper public data. Milestones 1, 2 (Carbon Mapper part), 3 (Carbon Mapper scenes), 5, 6 and 7 (production denominators from OGIM) are in; see `docs/BACKLOG.md` for the rest.

## Run it

```bash
cd methane-dd
../.venv/bin/python -m pytest tests -q                       # statistics and parsing tests, no network
../.venv/bin/python -m mdd.cli discover --area examples/delaware_demo.csv --out assets.csv   # propose assets (OGIM), edit, confirm
../.venv/bin/python -m mdd.cli run --area examples/delaware_demo.csv --volume 500000 --target 0.002 --confirmed assets.csv
```

The area can be a KML/KMZ (drawn in Google Earth; folder names are segment hints; a placemark named "customer" is the delivery point), GeoJSON, or a CSV of `lat, lon, label[, radius_m]`. Points are search areas (500 m default radius) unless labelled "asset"; lines are pipeline assets with a 250 m corridor. `--volume` is the customer's annual volume in MMBtu.

Each run writes `runs/<timestamp>-<name>/` with `assets`, `plumes`, `scenes`, `looks`, `attributed` and `per_asset` tables (Parquet), `scorecard.json`, `meta.json` and `report.html`. Raw API responses are cached with their retrieval date under `cache/` so a score can be reproduced.

## What the number means

- **Assured intensity** is the 95th percentile of delivered methane intensity: measured large sources (floor), the large sources that could have been missed given how many clear looks found nothing (ceiling, roughly 3/n), and a sub-threshold component that is a labelled prior unless supplier data are given.
- **Measured floor** is the 5th percentile of the floor alone: if it exceeds the target, the path fails on measured evidence regardless of certificates.
- **Grades:** Verified needs supplier low-threshold data; public data alone caps at "Screened, unverified"; detections that push the assured intensity over the target give "Flagged"; a floor over the target gives "Failed".

Satellites and aircraft cannot certify 0.2 % at a well pad (the whole budget is about 1.4 kg/h); expect production segments to come out "Flagged" or "Screened" on public data. That is the finding the spec anticipates (SPEC 1).

## Data and assumptions in v1

- Carbon Mapper public API: plumes with rates and scene footprints with cloud cover (Tanager-1, EMIT, AVIRIS-NG, AVIRIS-3, GAO). The API's date filter is applied locally. Footprints are scene bounding boxes; exact polygons are a backlog item.
- Assets and 2022 production from OGIM v3.0 (needs the GeoPackage in `../data/raw/ogim/`; falls back to the committed MRVSim site table).
- Wind: a default 3 m/s on every look until reanalysis wind is attached (flagged in the report).
- Sub-threshold literature fractions in `mdd/pipeline.py` are placeholders marked verify.
- Customer shares default to a proportional allocation within each segment so that share x throughput sums to the delivered volume; supply them explicitly for a real contract path.
