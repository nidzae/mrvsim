# 2026-10-03 — Real production sites, real production, site-based weights

Requested by Nidhi: the map looked sparser than real US production; use real well data including the smallest wells, real production per site, and do not under-weight small operators.

## What was built

- **Data.** OGIM v3.0 [ogim] (Zenodo 10.5281/zenodo.22835235, CC-BY 4.0, 3.4 GB, git-ignored) fetched by `data/scripts/fetch_ogim.py`. `data/scripts/build_sites.py` turns its 2022 production records into `data/fitted/sites.npz` (5 MB, committed): **577,007 onshore producing sites in 20 states** with location, gas and oil production, basin (EIA outlines), and type (gas energy share), plus location pools for 5,104 midstream facilities, a summary JSON, and a 0.1° density grid for the map.
- **Population.** Well-pad facilities are sampled from that table without replacement and carry the site's location and production; emissions remain simulated. Midstream facilities sit on real locations. 25 cells → 75 strata (was 21 → 63). `site_locations: box` restores the old behaviour.
- **Weights.** Well-pad cell and class weights are real site counts and gas volumes (`fit_strata_weights.py` re-run). 77 % of sites produce under 15 boe/d and carry 4 % of the gas; a unit test checks that the weighted sample reproduces both shares.
- **Map.** A brown background layer shows the density of all real sites (toggle in the legend); the hover tooltip and the drill-down state a dot's production and how many real sites it stands for.
- **Compatibility.** Runs store their strata table; runs saved before today load with the legacy 63-stratum layout (checked on `incidental95`).
- **Sharing.** `web/dist` is committed and the README has a "Run it in 5 minutes" section; a fresh clone was installed and a quick run completed (earlier today, before the site work).

## Effect (default mix, quick mode, run `20261003T164254Z-c2b72b41`)

| | Before (`incidental95`) | Now |
|---|---|---|
| Facilities generated / estimated | 1,890 / 630 | 2,250 / 750 |
| Calibration (mass / intensity) | 0.905 | 0.897 / 0.895 |
| Certified at the 0.2 % intensity bar (share of facilities) | 25.6 % | 17.6 % (37 % of throughput) |
| Fails / indeterminate (share of facilities) | — | 46 % / 36 % |
| GHGSat rows, of which incidental | 6,290 / 1,139 | 11,254 / 5,175 |
| Facilities seen by GHGSat only incidentally | 95 | 267 |

Real clustering multiplies incidental capture, and real production makes most small sites fail an intensity bar (they sell very little gas).

## Tests

- Unit suite: 148 passing. New: real-site sampling, weights reproduce the site population, midstream pools, saved strata round trip and legacy fallback. Updated: facility counts in the API estimate test (750 / 7,500), the cost test (incidental looks are free; it only passed before because the old layout had almost none).
- Validation V1–V7 was re-run **before** this change (`runs/validation/20261003T163433Z.json`): V4–V7 pass (V4 now passes), V1–V3 fail as on 2026-10-01. Re-run on the real-site population below.

## Docs changed

DECISION_LOG 2026-10-03 "Well-pad facilities are real production sites…"; TDD §3.1, §3.5, §13; PRD §7.6, §10 (Q2 resolved, Q5 resolved for well pads), §11; REFERENCES ([ogim] added, [basin-extents] narrowed); QUICKSTART; README; data/README; `configs/strata.yaml`, `configs/default.yaml`.

## Decisions for Nidhi

1. **Throughput class rule.** Default `count` (TDD: equal numbers of sites per class) makes the sample mirror reality, mostly small sites, but throughput-weighted shares then rest on about 29 effective facilities. `throughput` (equal energy per class) raises that to about 280 at the cost of the map and unweighted statistics over-representing large sites. Recommendation: switch to `throughput` together with making coverage shares, cost and the headline medians population-weighted.
2. **Well pads versus midstream share** still comes from GHGRP reported CH4, which omits small operators.
3. **Production-dependent emission rates.** Priors do not depend on a site's production; small and large sites in a cell draw from the same rate distribution.
4. **Neighbour density.** Incidental capture sees only sampled neighbours; a density correction (option 1 from the discussion) is not built.
5. The 50 m well-to-site linking distance is an assumption (`verify`).
6. Earlier open items stand: TROPOMI criterion, Quick mode as PRD N3, hosting (on hold), size-dependent intermittency.

## Blocked / not done

- The Chrome extension was not connected, so the new map layer and tooltip were checked through the API and MapLibre's style validator, not by eye.

## Validation on the real-site population (quick mode, `runs/validation/20261003T172540Z.json`)

| Test | Before today's change | Real sites | What changed |
|---|---|---|---|
| V1 | fail | fail | DJ survival curve now also outside a factor of 2; Permian, Appalachian and persistence fail as before. |
| V2 | fail | fail | Loss rates against Sherwin 2024: Appalachian 0.66 % vs 0.71 % [0.61, 0.81] **now passes**. Permian 0.48 % vs 1.89 %, DJ 0.03 % vs 1.10 %, Uinta 0.78 % vs 5.55 % are now far too low (before: 0.71 %, 0.50 %, 10.9 %). FEAST cross-check passes. |
| V3 | fail | fail | Haynesville and Appalachian pass; Permian, DJ and Uinta estimated intensities are below the published values (Uinta passed before). |
| V4 | pass | **fail** | Throughput-weighted certified share (intensity) 0.31 vs 0.41 between terciles and quintiles (2·SE 0.08); mass bias median just outside 2·SE. |
| V5 | pass | pass | κ 0.87–0.91 under each perturbation. |
| V6 | pass | **fail** | 2 of 60 events detected (was 6 of 60): mean estimate/truth 0.039 against a lower bound of 0.05. The 60 events reuse the first 60 facility locations, which changed; the target itself is still a placeholder marked verify. |
| V7 | pass | pass | κ 0.886 (mass), 0.888 (intensity). |

Reading: the estimator is still calibrated (V5, V7). The population now has real gas volumes in the denominator, and the emission priors, which were fitted per site without regard to site size, no longer reproduce basin loss rates in the Permian, DJ and Uinta: sites there are multi-well pads or leases with far more gas per site than the fit assumed. This makes decision 3 above (production-dependent emission rates, refitted against the real site table) the main open modelling item. The V4 failure is the throughput-weighting noise described in decision 1. V6 is a 60-event Monte Carlo sitting at the edge of a placeholder range.

