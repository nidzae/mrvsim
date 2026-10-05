# 2026-10-05 — Observation-only verdict

Requested by Nidhi: "toggle off priors, I want to know only actual observation"; after the vague-prior attempt failed, "go with [assumption-free bounds]; aerial only will not be enough, we need continuous monitoring to pass."

## What was built

- `mrvsim/score/observed.py` (PRD §5.3a, TDD §7a): per facility, bounds on annual emissions from the observation logs alone. Lower bound = what continuous monitors measured (low end of their error); upper bound = the same at the high end plus the detection limit for monitored hours without a detection, and **unbounded if any hour of the year was unobserved**. Certified iff upper bound ≤ bar; fails iff lower bound > bar. Snapshot detections are flags only.
- Computed in every run for every facility (no estimator), saved as `observed_bounds.npy`, served in the facility GeoJSON and detail; headline metrics by segment (well pads / midstream) for both the observation view and the estimate.
- UI: top-bar selector "verdicts: observations only / estimate", default observations only; map recolours client-side; facility panel has an "Observations only" block; dashboard has an observation panel above the estimate tiles.
- `scoring.unobserved_cap: throughput` keeps the withdrawn "cannot emit more than it handles" cap as an option (not default: reported gas is marketed gas, and it certified oil-dominant sites with no observation).
- Fix during testing: facilities reporting no gas have no intensity verdict (their intensity is undefined); before the fix, false detections there produced wrong "fails".
- Unit tests: 161 passing (4 new in `tests/unit/test_observed_bounds.py`).
- The vague-prior attempt is parked on branch `observation-only-wip` (not merged).

## Results (quick mode, bar 0.2 % intensity / 50 t/yr; shares of real facilities)

| Sensor mix | Year observed (avg) | Observation: certified / fails (intensity) | Estimate: well pads certified / fails | Estimate: midstream certified / fails |
|---|---|---|---|---|
| Aircraft 2×/yr + GHGSat top 30 % + TROPOMI (`20261005T220415Z-67ae8f2d`) | 0 % | 0 % / 0 % | 0.4 % / 31 % | 50 % / 36 % |
| … + monitors on top 20 % (`20261005T220624Z-ca272880`) | 16.5 % | 0 % / 1.4 % | 0.4 % / 31 % | 51 % / 37 % |
| … + monitors everywhere (`20261005T220917Z-459d9a75`) | 82.5 % | 0 % / 23 % (well pads 21 %, midstream 27 %) | 5.8 % / 44 % | 53 % / 39 % |

No observation-based verdict was wrong in any run (0 wrong fails of 28, 134, 377 and 414). Nothing is certified from observation in any mix: modelled monitors are usable about 82 % of hours (3 % outage, 85 % wind-sector coverage, both marked verify), so no site reaches a full year.

## Decision for Nidhi

Whether to allow a stated tolerance for monitoring gaps (for example: certify if at least X % of the year was observed and the measured part is under the bar, with the gap shown). Without it the observation view can only ever fail sites. With it, the tolerance is an assumption and should be shown as such.

## Docs changed

PRD §5.3a, §11; TDD §7a, §13; DECISION_LOG "Observation-only verdict"; QUICKSTART "Two kinds of verdict"; BACKLOG A9.

## Addendum: redundancy and the high-availability monitor (DECISION_LOG "Full-year coverage")

Nidhi asked why the observation view could only fail sites when enough sensors should give full coverage somewhere. Added: `redundancy` (independent monitor networks per facility, usable hours are the union, cost multiplies) and `cms_high_availability` (outage 0.005, every wind sector, 3x cost; assumptions). 162 unit tests passing.

| Sensor mix (quick, on top of the default aircraft + satellites) | Year observed (avg) | Observed every hour | Observation: certified / fails (intensity) | Observation: certified / fails (absolute) | Programme cost (sample basis) | Cost per facility certified from observation (intensity) |
|---|---|---|---|---|---|---|
| 2 generic monitors on top 20 % (`20261005T233527Z-36c3649e`) | 19 % | 0 % | 0 % / 1.8 % | 0 % / 7.3 % | $34 M | — |
| 2 high-availability monitors on top 20 % (`20261005T233820Z-f23c1f11`) | 20 % | 16 % | 9.3 % (19 % of gas) / 2.0 % | 3.1 % / 7.4 % | $61 M | $295 k |
| 2 high-availability monitors everywhere (`20261005T234113Z-7e0b001a`) | 100 % | 81 % | 12 % (29 % of gas) / 25 % | 45 % / 20 % | $221 M | $820 k |

No observation-based verdict was wrong in any run (certified: 0 wrong of 237, 95, 431, 791; fails: 0 wrong of 35, 144, 37, 148, 411, 448). Even with two high-availability networks everywhere 19 % of facilities miss at least one hour, and on intensity most fully observed sites stay undecided because the monitor's 4.5 kg/h detection limit over a year exceeds a small site's 0.2 % allowance.
