# 2026-10-03 — Equipment-based leak model for real sites

Requested by Nidhi: leak size and frequency should depend on site size and type "in a statistically accurate way"; age only if a verifiable source exists. Plan approved the same day.

## What was built

- **Wells per lease** (`data/scripts/build_sites.py`): Texas gas records are single wells (6-digit ids, confirmed in the data); Texas oil leases and the lease/unit records of OK, KS, LA, KY, MI get an estimated well count by assigning producing wells to the nearest production record of the same operator within 2 km. Site table total: 1,072,011 wells on 577,007 sites.
- **Fit** (`data/scripts/fit_equipment_priors.py` → `configs/priors/equipment_cells_2026-10-03.yaml`): per well class × productivity bin (34 cells), the number of emitting steady and episodic equipment categories per well and a lognormal for one emitter, from the 100 uncertainty realizations of [rutherford2021]; parameter spread across realizations and quantile fit errors recorded.
- **Model** (`mrvsim/population/equipment.py`, TDD §3.4a): per-site source count, steady and intermittent rate parameters and duty cycle from wells, class and bin; expected site emission is exact. Stored per facility and used by the estimator as its prior (`mrvsim/estimate/fast.py: facility_priors`).
- **UI**: the drill-down lists a site's wells, class and productivity bin.
- **Validation fix**: V2 and V3 weighted emissions by gas (an error introduced this morning with the gas-proportional throughput weights). Corrected.
- `population.leak_model: stratum` restores the previous priors.

## Checks

- Expected emission over all sites: 6.0 Tg/yr (1.07 M wells) vs 6.1 Tg/yr (1.0 M wells) in the source model.
- Sites under 15 boe/d: 37 % of well-pad emissions, loss rate 7.3 % vs 0.54 % for the rest. Published measurement-based figure is about half ([omara2022], verify).
- Default quick run `20261003T204728Z-517b1df8`: calibration 0.906 (mass), 0.907 (intensity); at the 0.2 % intensity bar certified 15.6 % of facilities (31 % of throughput), fails 26 %, indeterminate 58 %; median relative half-width 4.3 (was 2.7).
- Unit tests: 153 passing (5 new in `tests/unit/test_equipment_model.py`).

## Validation (quick mode, `runs/validation/20261003T210255Z.json`)

| Test | Result | Detail |
|---|---|---|
| V1 | fail | Detected-rate quantiles and survival curves fail in all four basins with targets, Uinta included: there is no super-emitter tail on top of the equipment model. Persistence fails. |
| V2 | fail | Well-pad loss rates (sample of 100 per stratum): Permian 1.15 % vs 1.89 %; Appalachian 0.37 % vs 0.71 %; DJ 0.58 % vs 1.10 %; Uinta 1.11 % vs 5.55 %. Published values include midstream and aerially detected super-emitters. FEAST cross-check 0.996 (pass). |
| V3 | fail | Haynesville, Appalachian and DJ pass; Permian (interval up to 1.14 % vs 1.89 %) and Uinta (up to 3.75 % vs 5.55 %) below published. |
| V4 | fail | One metric: intensity bias median differs by 0.065 between terciles and quintiles (2·SE 0.062). The throughput-weighted certified share now passes. |
| V5 | pass | κ 0.90–0.92 under every perturbation. |
| V6 | fail | Unchanged from this morning: 2 of 60 events detected; placeholder target. |
| V7 | pass | κ 0.890 (mass), 0.892 (intensity). |

## Correction to this morning's note

`2026-10-03-real-sites.md` reported Permian 0.48 %, DJ 0.03 %, Uinta 0.78 % and concluded the old priors made large basins look far too clean. Those numbers came from the V2 weighting error. With the corrected estimator the old priors gave roughly 3.0 % (Permian), 2.1 % (Appalachian), 0.6 % (DJ), 4.3 % (Uinta) for well pads. The case for the equipment model is that leaks should follow site characteristics, not that the old numbers were uniformly too low.

## Age

Not modelled. The data on disk contain no age–leak relationship, and a web search returned only the general statement that measurement studies find weak correlations between site emissions and age (not verified in primary sources).

## Open

1. **Aerial super-emitter tail** as an add-on fitted to the Sherwin production-site distributions (plan step 3). This is what V1 and the Permian/Uinta gaps in V2/V3 point to. The Sherwin release has production-only and midstream-only distributions per campaign, which also give a production-only target for V2.
2. Durations of episodic sources are the stratum's, not fitted to the liquids-unloading and tank event data on disk.
3. Wells per lease and the oil-only bin variable are flagged `verify`.
4. Earlier decisions still open: sampling class rule, satellite neighbour density, well-pad versus midstream share, hosting.

## Docs changed

DECISION_LOG 2026-10-03 "Equipment-based leak model…"; TDD §3.4a (new), §9, §13; PRD §11; REFERENCES ([omara2022], verify); QUICKSTART; `configs/default.yaml`.

## Addendum 2026-10-05 — aerial super-emitter tail (DECISION_LOG "Aerial super-emitter tail")

Built 2026-10-03 at Nidhi's choice ("per well by basin fitted to Sherwin"): `data/scripts/fit_aerial_tail.py` → `configs/priors/aerial_tail_2026-10-03.yaml`; TDD §3.4a. Per-well chance applies only to sites whose methane production is at least the survey's transition point; basin totals are anchored to the survey's above-transition loss rate. 155 unit tests passing. Default quick run `20261003T212043Z-d090a209`: calibration 0.908 (mass), 0.906 (intensity).

Validation (quick mode, `runs/validation/20261005T164610Z.json`). Earlier attempts on 2026-10-03/04 were cut off because the laptop slept; no code fault was found.

| Test | Result | Detail |
|---|---|---|
| V1 | fail | Quantile bands and survival curves fail in all four basins with targets; persistence fails (not fitted). |
| V2 | fail | Well-pad loss rates from a 60-per-stratum sample: Permian 1.41 % (published production-only 0.97 %, production + midstream 1.89 %); Appalachian 0.41 % (0.47 %, 0.71 %); DJ 0.90 % (0.72 %, 1.10 %); Uinta 1.63 % (4.50 %, 5.55 %). The pass rule compares with the production + midstream interval. FEAST cross-check 0.999 (pass). Sampled values are noisy: the expectation over every site is 1.37 %, 0.73 %, 0.95 %, 3.16 %. |
| V3 | fail | Haynesville, Appalachian and DJ pass; Permian (interval up to 1.17 % vs 1.89 %) and Uinta (up to 5.53 % vs 5.55 %, just short) fail. |
| V4 | fail | One metric: completeness 0.997 vs 0.983 between terciles and quintiles (2·SE 0.012). All certification, width, bias and calibration metrics are stable. |
| V5 | pass | κ 0.89–0.92 under every perturbation. |
| V6 | fail | Unchanged: 2 of 60 events detected; placeholder target. |
| V7 | pass | κ 0.893 (mass), 0.900 (intensity). |

Open after this: V1 needs a look at why the simulated detected-rate distributions miss the published bands even with the tail (the sample holds few sites above the campaign floors, and the tail is matched on mass and frequency above the transition point, not on the shape between the detection floor and the transition point); persistence; midstream in V2; the Uinta shortfall in sampled runs.
