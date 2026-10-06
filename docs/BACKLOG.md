# MRVSim backlog

Leftover work and open decisions as of 2026-10-05 (pencils down). Scope exclusions for version 1 are in PRD §3.2 and are not repeated here. Each item says what it is, why it matters, and what it needs. Update this file when an item is started or closed; record the outcome in `DECISION_LOG.md`.

State at this point (updated end of 2026-10-05): all eight build phases exist; 162 unit tests pass; validation V4–V7 pass and V1–V3 fail on population realism (`docs/status/2026-10-05-sampling-and-headline.md`); the observation-only verdict and monitor redundancy are in (`docs/status/2026-10-05-observation-only.md`).

## A. Decisions waiting on Nidhi

| # | Decision | Why it matters | Recommendation |
|---|---|---|---|
| A1 | **Satellite neighbour density.** Incidental capture counts only sampled neighbours inside a tasked scene; the sample holds about 1 in 250 real sites. | Free satellite and aircraft coverage is understated, so tasked sensors look worse value than they are. | Credit each scene with the real number of sites inside it, from the site table. Needs a go-ahead, not a design choice. |
| A2 | **Well pads versus midstream share.** The split of weight between well pads and each midstream type comes from GHGRP-reported CH4, which omits small operators. | Shifts headline totals; also blocks V2, whose published targets include midstream while the simulation's loss rates cover well pads only. | Replace with a measurement-based split. Needs a published source to be found and verified first. |
| A3 | **Relaxed TROPOMI acceptance test** (detects a 10 t/h source on more than half of clear passes, not nearly all). | A CLAUDE.md Phase 3 criterion was changed on the evidence of Schuit 2023. | Confirm. |
| A4 | **Quick mode as the meaning of PRD N3** (interactive recompute under 60 s). Quick runs take about 2 to 3 minutes; Full about 40. | N3 is not met as written. | Confirm the interpretation or set a different target. |
| A5 | **LDAR-Sim reuse** (PRD Q6): the hot path is native; LDAR-Sim is vendored for cross-checks only. | Provisional since Phase 3. | Confirm. |
| A6 | **Hosting online.** On hold at Nidhi's request (2026-10-03). | Sharing without a local install. | Google Cloud Run was the free-tier option discussed; needs a billing account and `gcloud` login by Nidhi. |
| A7 | **Is the decided share of emitted mass the right headline metric?** It is 94–98 % in the default mix. | It may not separate sensor mixes any better than completeness did. | Test it across mixes; if flat, restrict it to sites near the bar. |
| A8 | **"Current reality" default on load.** Raised 2026-10-05 and deferred. The default mix (aircraft twice a year on every site, one GHGSat monthly on the top 30 %, TROPOMI everywhere) is illustrative. Three choices are open: what the default contains besides satellites (satellites only, plus today's aircraft, or as now); how pointable satellites are tasked (capacity-limited, free mappers only, or per site as now); whether satellites without blind-test validation count by default. | The first screen suggests a monitoring system that does not exist. | Satellites only, capacity-limited tasking, all operating satellites shown with the unvalidated ones flagged. Depends on B16, B17 and C12. |
| A9 | **Verdicts resting on the prior.** Raised by Nidhi 2026-10-05. **Addressed 2026-10-05** by the observation-only verdict (PRD §5.3a): bounds from measurements alone, certification only with the whole year under continuous observation, shown next to the estimate and split by well pads and midstream. Still open: (i) whether to allow a stated tolerance for monitoring gaps, since modelled monitors are usable about 82 % of hours and so certify nothing; (ii) treating hourly monitor errors as independent, which would tighten the bounds; (iii) the abandoned vague-prior attempt is on branch `observation-only-wip` and can be deleted. | Core question of the experiment. | Decide (i) after looking at the first runs. **2026-10-05 later:** monitor redundancy and a high-availability monitor class added so full-year coverage is reachable (DECISION_LOG "Full-year coverage"); the gap tolerance (i) remains open. |

## B. Modelling work

| # | Item | Detail |
|---|---|---|
| B1 | **V1 still fails.** | Simulated detected-leak size distributions miss the published bands in the Permian, Appalachian and DJ. The aerial tail is matched on frequency and mass above the transition point, not on the shape between the survey detection floor and that point. Not yet diagnosed. |
| B2 | **Persistence of large events is not fitted.** | The duty cycle of tail sources is whatever the frequency and mass conditions require. The Cusworth persistence check in V1 fails. |
| B3 | **Durations of episodic sources are assumed.** | Liquids-unloading and tank-flashing events use the stratum's placeholder durations. Event data are on disk (`data/raw/rutherford2021/.../c_Other_Emissions`). |
| B4 | **Permian in V3** | Estimated intensity interval tops out at 0.99 % against a published 1.89 % (which includes midstream; see A2). |
| B5 | **V5 may not perturb the equipment model.** | The misspecification test perturbs stratum priors; well-pad rates now come from the equipment cells. Not checked. Extend the perturbations to the equipment parameters and the aerial tail. |
| B6 | **V6 rests on a placeholder.** | The VLMR release schedule was never fetched; 60 events is a noisy sample. |
| B7 | **Unbounded Pareto tail.** | A simulated event can exceed the site's own production; sampled basin totals are noisy. Truncating needs changes in the rate sampler, the SMC prior density and the PyMC model. |
| B8 | **Episodic mass lost at eligible sites.** | About 20 % of the bottom-up episodic mass at tail-eligible sites is dropped by the caps that keep the tail solve feasible; about 4 % of those sites end with a lognormal sigma above 4. |
| B9 | **Throughput precision is still modest.** | Effective sample size for gas questions is 160–185. More size classes or a larger sample raise it. |
| B10 | **Age of equipment or wells.** | Not modelled: no verified source. OGIM has completion dates if one is found. |
| B11 | **Size-dependent intermittency** (version-2 item from the 2026-10-01 priors fit). | Largely overtaken by the equipment model; revisit with B2 and B3. |
| B12 | **Basin-specific equipment factors.** | Equipment emission parameters are national; basins differ only through site mix and the aerial tail. |
| B13 | **Source-level sensors on multi-well sites.** | A site's emitters are pooled into at most about four sources, which changes what an OGI survey would see. |
| B14 | **Midstream throughput and locations.** | Midstream throughput is a placeholder lognormal; processing, transmission and storage use one national cell. |
| B15 | **Correlated weather and error within a campaign day or scene** (TDD §4.2 version-2 item). | Cloud is shared within a scene; wind and quantification error are independent per facility. |
| B16 | **Full satellite constellations.** | Each instrument is one spacecraft (GHGSat-C2, Sentinel-2A, Landsat 9, Tanager-1). Model every operating satellite with its own element set; the overpass cache and the sensor YAML need a list of satellites per sensor. |
| B17 | **Tasking capacity for pointable satellites.** | No limit on targets per satellite per day and no competing customers; `_select_taskings` treats each facility independently. Needs a capacity per satellite and an allocation rule. |
| B18 | **Orbit epoch and simulated year.** | One element set (epoch 2026) is propagated over the simulated year 2024. Align the simulated year with the epoch, or refresh elements per run; pass dates are representative either way. |

## C. Data items flagged `verify`

| # | Item | Where |
|---|---|---|
| C1 | 50 m well-to-site linking distance | `data/scripts/build_sites.py` |
| C2 | Wells per lease in TX oil leases, OK, KS, LA, KY, MI (nearest same-operator match within 2 km) | `data/scripts/build_sites.py` |
| C3 | Oil-only productivity bins read as oil bbl/d per well | `data/scripts/fit_equipment_priors.py` |
| C4 | Pooled aerial-tail values for the six basins without a survey | `data/scripts/fit_aerial_tail.py` |
| C5 | Omara 2022 cross-check numbers taken from secondary reports | REFERENCES `omara2022` |
| C6 | States with no production records in OGIM (AL, IL, IN, NE, TN, VA among them) are absent | site table |
| C7 | Aircraft survey block (0.5 km for Bridger; none for Kairos, AVIRIS-NG) | PRD Q7 |
| C8 | Tanager pointing angle (29°, derived) and PRISMA / EnMAP pointing | PRD Q8 |
| C9 | Sensor POD blocks are published summary thresholds, none fitted to per-release tables | sensor YAMLs |
| C10 | Condition priors (cloud, wind, surface) are placeholders; MODIS and ERA5 fetches need credentials | `data/README.md` |
| C11 | Remaining `verify` rows in `REFERENCES.md` (shown flagged in the Attribution panel) | REFERENCES |
| C12 | Sensor library not audited against the satellites operating today (for example EMIT is absent); constellation sizes and tasking capacities need sources | `configs/sensors/`, TDD §5.1 |

## D. Product and engineering

| # | Item | Detail |
|---|---|---|
| D1 | **New map layer and tooltips not checked by eye.** | The Chrome extension was never connected; the site-density layer, tooltips and dashboard tiles were checked through the API and MapLibre's style validator only. |
| D2 | **Old runs are not comparable.** | Runs before 2026-10-05 used unweighted width, facility shares and cost, and sample-based coverage. They still load. Consider marking them in the run selector. |
| D3 | **Windows instructions in the README are untested.** | |
| D4 | **Long runs stall when the laptop sleeps.** | Wrap in `caffeinate -dis` and keep the lid open; background tasks started by Claude stop after 2 hours. |
| D5 | **Gap-analysis prompt** does not yet describe the equipment model, the aerial tail, the weighted sample or the new headline metric. | `mrvsim/api/server.py` |
| D6 | **Full-resolution reference run** on the current model has not been made (about 40 minutes or more). | |
| D7 | **Optimizer and tornado** have not been re-run since costs and coverage became population-weighted. | |
| D9 | **Delete branch `observation-only-wip`** once nobody needs the parked vague-prior attempt. | Avoids confusion. | |
| D10 | **Validation V1–V7 not re-run** since the observation view and monitor redundancy were added (they do not change the estimator, so results should be unchanged; confirm). | | |
| D11 | **Literature review of comparable tools** (web search 2026-10-05, abstracts only). Closest: LDAR-Sim v3 (Highwood; equivalence of LDAR programmes, satellites and aircraft via METEC POD curves), FEAST, Arolytics AROfemp (commercial; GHGSat tiered equivalence, Permian 2023), MiQ equivalency process, continuous-monitor placement frameworks (CSU METEC), satellite OSSEs (HyGAS, multi-sensor flux frameworks), Sherwin 2024 ROAMS, GTI Veritas and OGMP 2.0 Level 5 protocols, DOE reports (Sandia SAND-2024-13381; GTI integrated platform design). None found that produces per-site annual estimates with calibrated intervals, a certification verdict and cost across sensor tiers. Read before expert review: Hodshire et al. 2025 (intermittent emissions vs periodic surveys, ACS ES&T Air); 2026 systems review of fugitive methane monitoring (Remote Sensing 18, 2433). | Supports the novelty claim in outreach. | A systematic review, including the full DOE reports, before any publication. |
| D8 | **`web/dist` is committed**: rebuild and commit it after every front-end change. | README, Development |

## Where to look

- Decisions and their reasons: `docs/DECISION_LOG.md` (entries of 2026-10-03 and 2026-10-05 cover real sites, the equipment model, the aerial tail and the sampling design).
- Method: `docs/TECHNICAL_DESIGN.md` §3.1, §3.1a, §3.4a, §7.
- Latest state and validation tables: `docs/status/2026-10-05-sampling-and-headline.md`.
