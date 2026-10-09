# Backlog (methane-dd)

Milestones from SPEC 8c, with status on 2026-10-09.

| # | Item | Status |
|---|---|---|
| 1 | Area loader, OGIM discovery, confirmation table | **Done** (CSV confirmation file; map view not built) |
| 2 | Detection ingest | Carbon Mapper **done**; MAPL-EMIT database (Earth Engine), EMIT L2B (NASA CMR/Earthdata), IMEO (CSV; licence question SPEC 8e), MethaneSAT (Earth Engine on request) open |
| 3 | Observation ingest | Carbon Mapper scenes **done** (bounding boxes); exact polygons from `/catalog/download/scenes.gpkg`; MAPL-EMIT inference over EMIT granules; MethaneSAT L3 coverage; ERA5 wind per look open |
| 4 | Rate estimation for plumes without a rate; attribution with OGIM neighbours | Attribution **done** (distance-weighted among candidates on the path; neighbours off the path not yet added as competing candidates); rate from enhancement open |
| 5 | Duty-factor and rate model | **Done** (grid posterior, Jeffreys and uniform priors, POD from config) |
| 6 | Monte Carlo roll-up, grades, HTML report | **Done** |
| 7 | Denominators | Production from OGIM 2022 **done**; state agencies (monthly), pipeline throughput, customer delivered volume checks open |
| 8 | Distribution segment (Subpart W, PHMSA); optional LNG | Open |
| 9 | Supplier measurements and event logs | Open (schemas not written) |
| 10 | Hierarchical model benchmarked against Carbon Mapper's | Open |

Other items:

- Streamlit interface (SPEC 8a) once the numbers are trusted.
- Time-of-day bias flag and per-year trend reporting (SPEC 6e).
- Sub-threshold literature fractions are placeholders (`mdd/pipeline.py`); replace with EEMDL / Rutherford measurement-informed values with citations.
- POD parameters for Tanager, EMIT, AVIRIS-3 and AEMIS are marked verify; confirm against Duren et al. 2025 and METEC results.
- Published reproduction test (SPEC 8d second test) not written.
- Off-path OGIM neighbours as competing attribution candidates (SPEC stage 6 "weight down when other infrastructure sits nearer").
- The Carbon Mapper API ignores the date parameters we send; filtering is local. Check the documented parameter names.
