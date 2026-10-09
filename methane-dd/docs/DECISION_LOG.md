# Decision log (methane-dd)

## 2026-10-09 — v1 on Carbon Mapper public data, command line first
**Decision:** Build the pipeline of SPEC section 5 as a Python package `mdd` in the MRVSim repository (shared sensor models, shared OGIM data), command line first (SPEC 8). Carbon Mapper's public API is the first and, for now, only observation source: it returns plumes with rates and scene records with bounds and cloud cover without a key, which gives both detections and non-detections. MAPL-EMIT, EMIT L2B, IMEO and MethaneSAT need accounts, Earth Engine or Kaggle access and are backlog.
**Reason:** the spec names observation records as the binding gap; Carbon Mapper already supplies them. Starting there gives an end-to-end run with real data on day one.
**Alternatives:** separate repository (would duplicate the POD code and the 3.4 GB OGIM download); Streamlit first (the spec says numbers before interface).

## 2026-10-09 — Drawn points and polygons are search areas, not assets
**Decision:** Area features become assets only if they are lines (pipeline routes) or carry "asset" in their label; otherwise the assets inside them come from OGIM discovery. **Reason:** the first demo run double-counted a drawn "pad cluster" point as a facility with full customer share and no throughput.

## 2026-10-09 — Default customer shares by proportional allocation
**Decision:** When no confirmed-asset file is given, each segment's assets get customer share = delivered methane / segment throughput (capped at 1), so share x throughput sums to the delivered volume (SPEC 4b rule 3); the report states the allocation. With a confirmed file, shares are the user's and a 20 % inconsistency is warned. **Reason:** with share 1 on every discovered asset the first run charged a 500,000 MMBtu customer with the emissions of 47 assets handling ten times that, giving a 33 % intensity.

## 2026-10-09 — Scene footprints as bounding boxes; default wind
**Decision:** v1 uses each scene's bounding box as its footprint and 3 m/s wind on every look, both flagged in the report. **Reason:** the scene polygons are in a separate GeoPackage download and reanalysis wind needs a CDS account; both are backlog. Aircraft scenes are long diagonal strips, so bounding boxes overstate coverage: looks are over-counted and ceilings are slightly too tight until polygons are used.

## 2026-10-09 — Detections without a rate use the basin size prior
**Decision:** A Carbon Mapper plume published without `emission_auto` counts as a detection with the basin's detected-size prior for its rate, and the asset record says so. **Reason:** SPEC stage 4 (rate from enhancement and wind) is not built; dropping the detection would bias the duty factor low.
