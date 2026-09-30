# Sensor library

One YAML per sensor class (TDD §4). Fields: POD curve (logistic in ln q_eff with wind exponent
γ and surface adjustment φ), quantification error (β, σ), false-positive rate, operating
constraints, observation mode, spatial scope, schedule, cost, validation tier, and for
satellites an orbit block for the Skyfield overpass model (TDD §5.1).

**Provenance status of every numeric block is recorded in the file.** As of 2026-09-30 no
block is `fitted`: all POD and error parameters are `summary` values derived from published
summary statistics recalled while drafting, and every citation with `verify` status in
`docs/REFERENCES.md` must be checked against the paper before public use. Replace a block
with `status: fitted` by running `mrvsim.sensors.fit.fit_pod_logistic` on the paper's
release table and pasting the `fit_record`.

`pod50_kg_h` / `pod90_kg_h` are converted to (a, b) at load time (`PODCurve.ab_from_pod50_pod90`).
`ratio_95_low` / `ratio_95_high` are converted to (β, σ) (`QuantificationError.from_ratio_interval`).
