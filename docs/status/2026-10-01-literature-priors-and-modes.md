# Status — Literature pass, fitted priors, compute modes

Date: 2026-10-01 (requested by Nidhi: quick/full compute toggle; real numbers for sensor specs and emission priors)

## Compute modes (done)

- `POST /api/run` takes `mode: quick | full | custom`; `POST /api/estimate` returns a time estimate. Quick = 10 facilities/stratum, 2,000 draws, R = 3 (~1–2 min); Full = all 6,300 facilities, 10,000 draws, R = 5 (~40 min). The Sensors panel has the toggle with the estimate; Dashboard has **Re-run this mix at full resolution**.
- Facility coordinates are now fixed across replications (emissions still vary), so the ~100 s satellite overpass computation is cached after the first replication of a run.

## Sensor specifications (done; every block now cites a fetched source)

| Sensor | Before (recalled) | Now (published) | Source |
|---|---|---|---|
| Bridger GML | POD50/90 2/8 kg/h | 1/3 kg/h (>90 % POD at 3 kg/h, ~1 kg/h sensitivity); 95 % error −64.1 %/+87.0 % | Johnson 2021 RSE; Daniels 2023 |
| Insight M (Kairos) | 4/15 | 6/15 at 3 m/s (all releases > 5 kg/h per m/s detected; smallest 3.40, largest missed 10.47; slope 1.13) | El Abbadi 2024 |
| Carbon Mapper (AVIRIS-NG/GAO) | 15/60 | 8.5/12 (largest missed 6.61, smallest detected 10.92; slope 0.89, R² 0.61) | El Abbadi 2024 |
| GHGSat-AV (new) | — | 5/20 (smallest 2.91; false negatives at 15–30) | El Abbadi 2024 |
| GHGSat-C2 | 150/500 | 150/300 (0.197 t/h detected, quantified within 13 %) | Sherwin 2023 |
| Sentinel-2 | 2,500/7,000 | 1,000/2,500 (1.4 t/h by 3 of 4 teams; ~4 t/h by all) | Sherwin 2023 |
| Satellite error | −60/+90 % | −68 %/+110 % pooled, slope 0.855, no false positives | Sherwin 2023 |
| TROPOMI | 2.5/5 t/h (pinned by test) | 6/10 t/h (limit ~5 t/h favourable; detected 5th percentile 8 t/h) | Schuit 2023 |
| CMS | 0.6/2.5 | 1.5/4.5 kg/h (DL90 2.7–5.9 in 6 of 8; 3.9–6.2 best four; FP 6.9–13 % of reports) | Bell 2023; Ilonze 2024 |
| OGI | 0.02/0.10 | 0.035/0.134 kg/h (90 % POD at 7 scfh, experienced surveyors; 4.1 % FP) | Zimmerle 2020 |

`REFERENCES.md`: 14 entries moved to `ok` with DOIs/volumes/pages; `johnson2021` added; `chen2024` not located; Ravikumar is 2019 (key kept); Zimmerle is EST not JAWMA. **Still no block is `fitted`** to a per-release table; these are published summary thresholds.

**CLAUDE.md acceptance test changed (needs confirmation):** TROPOMI detects a 10 t/h source on *most* (> 50 %) clear passes, not nearly all; the literature does not support the original criterion.

## Emission priors (done: `configs/priors/fitted_2026-10-01.yaml`, provenance FITTED)

Data obtained: Rutherford 2021 code/database (Zenodo 4903897, 91 MB), Sherwin 2024 correction release (Zenodo 17968370, 467 MB; Tables S10/S21 and site-level CDFs for 15 campaigns), GHGRP RY2023 (already fetched), Cusworth 2022 Table 1 (via Europe PMC).

- Steady sources: lognormal μ₀ = −2.97 (0.051 kg/h), σ₀ = 2.09 from 3,082 emitting components.
- National well pads: λ = 2.1 sources/site, 21 % intermittent, μ₁ = 1.94, σ₁ = 1.18, Pareto above 180 kg/h with α = 1.56, mean off-period ≈ 830 h (duty cycle ~2 %) — matched to the Omara national site distribution (median 0.33 vs target 0.32 kg/h; p99 15 vs 16; top-1 % share 26 % vs 25 %).
- Per basin (survival curves of the Sherwin 2024 CDFs, within ~10 % over 0.1–100 kg/h): Permian λ = 4.5, 70 % intermittent, μ₀ shifted to −2.14; Appalachian λ = 1.9, 45 %; DJ λ = 1.0, 6 %; Uinta λ = 3.3, 45 %; Fort Worth → `other`. Cusworth persistence is a diagnostic (Permian 0.36 vs 0.26; Appalachian 0.36 vs 0.60): a single intermittent share per stratum cannot match both the low-level emitting fraction and large-source persistence (version-2 item: size-dependent intermittency).
- Midstream: GHGRP reported CH₄ per facility (processing median 10.5, transmission 12.6, storage 17.1 kg/h; gathering operator-basin aggregates 61 kg/h); bottom-up, under-reports ~2×.
- Not fitted: τ, σ_ν, ν_on, throughput, conditions (still placeholders, flagged in the file).

**Effect:** the placeholder priors were ~20× too high at the median well-pad rate. With fitted priors, default-policy calibration is 0.90 (mass) and 0.91 (intensity); well-pad intervals are wide (w ≈ 3: two aircraft passes constrain a 0.3 kg/h site poorly).

## Validation targets filled (`data/fitted/validation_targets.yaml`)

Sherwin 2024 Table S10 loss rates with CIs for Permian (fall 2021: 1.89 % [1.73, 2.06]), Appalachian (0.71 % [0.61, 0.81]), DJ (1.10 % [1.0, 1.24]), Uinta (5.55 % [5.15, 5.99]), Fort Worth (1.88 %); detected-site quantiles and survival curves per basin; Cusworth persistence (Permian 0.26, Marcellus 0.60), point-source share (~40 %), duration shares; Omara national quantiles; Hajny Haynesville 0.79 % confirmed. The Permian 4.6 % intensity in TDD §9 was not found in the Hajny preprint and is excluded. V1 now scores survival curves and Cusworth-style persistence; V2 scores basin loss rates.

## Validation run

Quick V1–V7 run against the fitted priors started 2026-10-01 (results in `runs/validation/`, see follow-up).

## Tests

128 unit tests; several updated for the fitted priors (identifiability now tested on mass, where facility-level CMS summaries are informative; zero-emission facilities allowed; Bridger error interval; TROPOMI threshold; budget uses common random numbers).
