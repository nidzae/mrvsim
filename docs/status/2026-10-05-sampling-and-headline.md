# 2026-10-05 — Over-sampling with weights; decided share of emitted mass on the headline row

Requested by Nidhi: remove completeness from the headline ("distracting") and show the share of emitted mass at sites with a decided outcome; over-sample big sites with weight adjustments, explained step by step in the PRD and technical design.

## What was built

- `population.throughput_class_rule: throughput` is the default: classes inside each basin × type cell hold equal shares of produced energy.
- Population-weighted statistics (TDD §3.1a, §7): facility shares, width and bias medians (count weights), throughput shares (throughput weights), cost on the basis of n facilities in population proportions. Calibration unweighted.
- Policy coverage refers to the real population (`policy.coverage_weighting: population`, written into new runs' configs; older runs keep the sample meaning and still load).
- `decided_share_emitted_mass` per KPI (PRD §5.5a) replaces completeness on the dashboard and in V4; completeness is still computed and stored.
- Dashboard labels: "Certified share (real facilities)", width for the typical real site and the typical unit of gas, cost basis stated.
- 157 unit tests passing (2 new: weighted shares and decided share; population-weighted coverage).

## Effect (default quick mix, run `20261005T173827Z-4089c730`)

| | Intensity | Absolute |
|---|---|---|
| Calibration | 0.897 | 0.904 |
| Certified, share of real facilities | 20.3 % | 63.0 % |
| Certified, share of gas | 42.5 % | 25.5 % |
| Fails / indeterminate, share of real facilities | 32.0 % / 47.8 % | 24.2 % / 12.8 % |
| Decided share of emitted mass | 93.8 % | 97.7 % |
| Median half-width, real site / unit of gas | 3.43 / 1.74 | 3.34 / 1.72 |

Not comparable with earlier runs on width, facility shares or cost (those were unweighted). The decided share is high because most emitted mass sits at large emitters that clearly fail; whether it separates sensor mixes has not been tested.

## Validation (quick mode, `runs/validation/20261005T180215Z.json`)

| Test | Result | Detail |
|---|---|---|
| V1 | fail | Quantile bands fail in Permian, Appalachian, DJ, Uinta (more simulated sites above the floors now: 141, 73, 53, 37). Survival curve passes in Uinta only. Persistence fails (not fitted). |
| V2 | fail | Well-pad loss rates: Permian 1.23 % (published production-only 0.97 %, production + midstream 1.89 %); Appalachian 0.27 % (0.47 %, 0.71 %); DJ 0.98 % (0.72 %, 1.10 %); Uinta 3.56 % (4.50 %, 5.55 %). Pass rule compares with production + midstream. FEAST cross-check 1.006 (pass). |
| V3 | fail | Haynesville, Appalachian, DJ and Uinta pass; Permian fails (interval up to 0.99 % vs 1.89 %). |
| V4 | **pass** | Every headline metric stable between three and five classes, including the throughput-weighted certified share (0.315 vs 0.315; 0.413 vs 0.417) and the decided share. |
| V5 | pass | κ 0.87–0.91 under each perturbation. Possibly weaker than before for well pads: their rates come from the equipment model, which the perturbations of the stratum priors may not reach (not checked). |
| V6 | **pass** | 9 of 60 events detected; mean estimate/truth 0.16 (range 0.05–0.8). The events sit on the first 60 facility locations, which changed with the sampling rule; the target is still a placeholder. |
| V7 | pass | κ 0.903 (mass), 0.902 (intensity). |

Five of seven pass (V4–V7 and, within V3, four of five basins). What remains is population realism: V1's detected-rate distributions and persistence, the Permian in V3, and V2's well-pad-only comparison against production-plus-midstream targets.

## Open

- Satellite neighbour density; well-pad versus midstream share (also needed for V2); persistence and episodic durations; V5 for the equipment parameters; whether the decided share discriminates between sensor mixes.

## Docs changed

DECISION_LOG 2026-10-05; TDD §3.1a (new), §7, §8.1, §13; PRD §5.5, §5.5a (new), §7.6, §11; QUICKSTART; `configs/default.yaml`.

## Addendum (same day): what the certified share is made of, and an observation-only attempt

- **The headline "certified share of real facilities" (20 % at the 0.2 % intensity bar) is almost entirely midstream.** Split by type in one replication of the default run: well pads 0.4 % certified, 28 % fail, 72 % indeterminate; midstream 46 % certified. Midstream holds 41 % of the facility weight by the GHGRP convention and has placeholder throughput (median about 20 MMcf/d), so a 0.2 % bar allows 50 kg/h or more. The headline should be split by segment (BACKLOG A9, A2).
- **Reliance on the prior.** 35 % of certified facilities had a prior that already certified them with no data; the "prior-only" flag caught 1.5 %, because it measures narrowing, not whether the verdict depended on data.
- **Observation-only attempt (branch `observation-only-wip`, not merged, not for use).** Replacing the population priors with one vague prior for every site, plus a rule that a verdict must be moved across the bar by the data. Result on one replication, default mix: with a prior centred on 1 kg/h per source, 1.4 % of well pads certified on the absolute KPI and 44 % "failed" on intensity with no observation behind it; with a prior centred on 0.14 kg/h, 97 % certified on the absolute KPI, calibration 0.80–0.82, and 48 of 504 certifications wrong. Adding continuous monitors on 20 % of facilities changed almost nothing. Conclusion: a vague prior is still a prior and its arbitrary centre decides the outcome; a flat prior on duty cycle is optimistic about rare events. The approach was abandoned; options are in BACKLOG A9.

