# Decision Log

Append-only. Newest at the bottom. Every entry that changes a requirement must also update `PRD.md`; every entry that changes a computation must also update `TECHNICAL_DESIGN.md`. Reference the PRD/TDD section changed.

Format:

```
## YYYY-MM-DD — Short title
**Decision:** what was decided
**Reason:** why
**Alternatives rejected:** what else was considered
**Docs updated:** PRD §x / TDD §y
**Status:** active | superseded by <date entry>
```

---

## 2026-09-30 — Scope to US onshore oil and gas, measurement layer only
**Decision:** Version 1 covers US onshore oil and gas facilities and the Measurement layer of MRV. Agriculture, waste, coal, offshore, non-US, Reporting, Verification, ownership mapping, governance, and regulatory benchmarking are backlog.
**Reason:** Focus; the KPI and estimator are the hard parts and are sector-agnostic in structure.
**Alternatives rejected:** Global O&G first (data too sparse outside US); full MRV first (R and V depend on M being solved).
**Docs updated:** PRD §3.2
**Status:** active

## 2026-09-30 — Two KPIs: intensity and absolute emissions
**Decision:** Report both methane intensity (NGSI loss rate) and absolute annual emissions per facility, each with a 90% credible interval.
**Reason:** Roughly half of O&G methane comes from oil production, where marketed-gas intensity is undefined or allocation-dependent.
**Alternatives rejected:** Intensity only (misses oil-side emissions); per-BOE intensity as third KPI (reintroduces allocation; deferred).
**Docs updated:** PRD §5, §7.1
**Status:** active

## 2026-09-30 — Certification rule uses the 90% upper credible bound and a precision requirement
**Decision:** Certified at bar B iff upper 90% bound ≤ B and relative half-width ≤ w_max; otherwise indeterminate or fails.
**Reason:** Separates well-measured from poorly-measured facilities; makes the number falsifiable.
**Alternatives rejected:** Point-estimate grading (current MiQ practice); 95% bound (tighter, but doubles cost for marginal benefit at this stage — revisit).
**Docs updated:** PRD §5.3–5.4
**Status:** active

## 2026-09-30 — Empirical POD curves instead of 3D plume simulation
**Decision:** Sensors are represented by logistic POD curves and lognormal quantification error fitted to blind controlled-release data. No dispersion modeling.
**Reason:** The controlled releases already integrate the physics; a dispersion model adds unvalidated error and compute.
**Alternatives rejected:** Gaussian plume per observation (FEAST-style); LES microscale modeling.
**Docs updated:** PRD §7.3; TDD §4.1
**Status:** active

## 2026-09-30 — Fork LDAR-Sim v4 as the simulation engine
**Decision:** Fork LDAR-Sim (MIT), disable repair, add conditions/estimator/scoring modules. FEAST as cross-check.
**Reason:** Maintained, validated, MIT-licensed, already has METEC POD integration and agent-based scheduling.
**Alternatives rejected:** Build from scratch; fork FEAST (MATLAB heritage, less active).
**Docs updated:** PRD §7.5; TDD §1
**Status:** active

## 2026-09-30 — Stratified sample, not full census
**Decision:** Simulate ~100 facilities per stratum (basin × facility type × throughput tercile) and weight.
**Reason:** Compute; facilities within a stratum share statistics.
**Alternatives rejected:** Every US well (unnecessary and slow).
**Docs updated:** PRD §7.6; TDD §3.1
**Status:** active

## 2026-09-30 — Calibration is the primary success metric
**Decision:** A configuration is acceptable only if 0.85 ≤ κ ≤ 0.95; precision is optimized second.
**Reason:** Overconfident intervals certify failing facilities.
**Docs updated:** PRD §7.7; TDD §7
**Status:** active

## 2026-09-30 — Version-2 items carried from TDD §11
**Decision:** The following are deferred to version 2, in priority order: (1) per-campaign correlated quantification error; (2) full HMM likelihood for continuous monitors; (3) renewal-process transition probabilities for closely spaced snapshots; (4) diurnal/seasonal emission structure; (5) heavier-tailed duration distributions; (6) negative-binomial source counts; (7) measured surface-adjustment factors if data become available.
**Docs updated:** TDD §11
**Status:** active

## 2026-09-30 — Q4 resolved: LDAR-Sim v4 has no satellite scheduler; MRVSim computes overpasses itself with Skyfield
**Decision:** Satellite observation opportunities are computed inside MRVSim by propagating public TLEs with Skyfield (TDD §5.1 as written). Nothing from LDAR-Sim v4 is reused for satellite scheduling. PRD open question Q4 is closed with the answer "neither: v4 has no satellite module".
**Reason:** Inspection of the vendored LDAR-Sim v4.2.4 tree (`vendor/ldar_sim`, upstream master 2026-06-16): `Deployment_Types.ORBIT = "orbital"` exists in `src/constants/param_default_const.py` but is never dispatched — `input_manager.py` and every `programs/*method.py` branch only on `mobile` and `stationary`, and the user manual lists only those two deployment types. The README's `orbit_predictor` dependency line is a holdover from the legacy V3 branch, whose `methods/deployment/orbit_crew.py` did propagate TLEs (15-minute steps, bounding-box swath polygons, daily viewability + cloud + daylight gates). V3 was therefore orbit-based, confirming the TDD design, but it ran at daily resolution and is not carried into v4. Additionally, v4's METEC wind-dependent POD sensor (`sensors/METEC_Wind_sensor.py`) is entirely commented out ("TODO: figure out weather").
**Alternatives rejected:** Porting V3's `orbit_crew.py` (daily resolution, per-site Python loops, `orbit_predictor` and `shapely` dependencies; MRVSim needs hourly resolution and vectorised swath tests); fixed revisit cadence (loses the local-time and cloud coupling that makes satellite completeness realistic).
**Docs updated:** PRD §10 (Q4 row); TDD §5.1 unchanged (already Skyfield). Adds PRD Q6 on the scope of the LDAR-Sim fork.
**Status:** active

## 2026-09-30 — Phase 0 seed derivation scheme
**Decision:** Every random stream is a PCG64 `Generator` obtained from a `SeedTree(master_seed)` by a named path (e.g. `("population", "rates", stratum=3)`); the path is BLAKE2b-hashed into a `SeedSequence` spawn key. Streams are independent of the order in which they are requested. Libraries that need an integer seed (PyMC, Optuna) get one from the same tree. A run directory `runs/<id>/` stores canonical config YAML, manifest (seed, config SHA-256, git SHA + dirty flag, dependency versions, per-stage and total elapsed time), and byte-stable outputs (`.npy`, `.json`, `.csv`).
**Reason:** PRD N4 requires byte-identical reproduction from config and seed. Order-independent named streams mean vectorised and loop implementations of the same stage agree, and a later phase adding a stream cannot perturb earlier stages' draws.
**Alternatives rejected:** A single global `Generator` threaded through stages (draw order becomes part of the spec; any refactor changes results); `SeedSequence.spawn()` counters (order-dependent).
**Docs updated:** none required (implements PRD N4); recorded here for the implementation contract. Unit test `tests/unit/test_seeds.py::test_raw_bitstream_is_frozen` pins the scheme.
**Status:** active

## 2026-09-30 — Phase 1 implementation choices for the population generator
**Decision:** Four choices made while implementing TDD §3, none changing an equation:
1. **Stationary start of the renewal process (TDD §3.4).** Each intermittent source starts on with probability π and the residual of its current interval is U·D*, with D* drawn from the length-biased lognormal (ν + τ², τ). This is the exact stationary state of a renewal process with lognormal intervals, so no burn-in is needed and the first day of the year has no transient.
2. **Pareto splice implementation (TDD §3.3).** Draw q from the lognormal; where q > q_tail replace it with q_tail·U^(−1/α). This preserves P(q > q_tail) from the lognormal and makes the conditional tail exactly the TDD's Pareto statement.
3. **Hourly discretisation.** S_ij(t) is the process state at the midpoint of hour t; M_i = Σ_t Q_i(t) uses these hourly states. Sub-hour on/off structure is lost, which only matters for cycles shorter than ~2 h.
4. **Throughput classes as quantile bands (TDD §3.1).** V_gas for class k of N is drawn from the cell's lognormal restricted to the [k/N, (k+1)/N) quantile band by inverse CDF, so classes are terciles/quintiles by construction and V4 can switch N without refitting. X_CH4 is clipped at 1.0 (mole fraction), a numerical guard on TDD §3.5.
**Reason:** Each is the simplest exact or near-exact realisation of the written equation that vectorises over all sources.
**Alternatives rejected:** Burn-in simulation for stationarity (slow for long cycles, approximate); mixture-density splice (changes P(q > q_tail)); per-hour Bernoulli(π) states (loses run lengths that CMS likelihoods need); fitting separate lognormals per tercile (triples the parameters to fit).
**Docs updated:** TDD §3.4 (stationary-start note), TDD §13 change log; REFERENCES adds `renewal-theory`, `eia-heat-content`, `basin-extents` (all `verify`).
**Status:** active

## 2026-09-30 — Default strata list and placeholder weights
**Decision:** The default sample uses 21 basin × facility-type cells × 3 throughput terciles = 63 strata (`configs/strata.yaml`), matching the TDD §10 budget of ≈60. Stratum weights are equal within a facility type and marked PLACEHOLDER; `data/scripts/fetch_ghgrp_subpart_w.py` (which succeeded, 6,738 Subpart W facility rows for reporting year 2023) plus state production data will replace them.
**Reason:** A full 10 basins × 7 types × 3 classes grid is 210 strata, 3.5× the compute budget, and many cells are empty in reality (e.g., storage in the Bakken).
**Alternatives rejected:** Full grid with n_h scaled down (violates the n_h ≥ 30 minimum); collapsing facility types (loses the per-type slices PRD F10 needs).
**Docs updated:** TDD §3.1 (cell list note), TDD §13.
**Status:** active

## 2026-09-30 — Stratum weights fitted from GHGRP Subpart W RY2023
**Decision:** `data/fitted/strata_weights.json` (provenance FITTED) supplies the cell weights; the equal placeholder weights in `configs/strata.yaml` are used only when that file is absent. Method (`data/scripts/fit_strata_weights.py`): cells are weighted within each facility type by producing wells (well pads), operator-basin reporter count (gathering), or facility count (processing, transmission, storage); type groups are then weighted by their share of reported Subpart W CH4. Throughput weights use gas sold, gas transported, or gas received where the table has them, else fall back to the count weight (flagged). AAPG basin codes are mapped to MRVSim basins (Arkla + East Texas → Haynesville; Gulf Coast → Eagle Ford as an upper bound); gas/oil/mixed well-pad assignment uses the energy share of reported sales.
**Reason:** Real relative weights were obtainable with no credentials; the Envirofacts tables `EF_W_EMISSIONS_SOURCE_GHG`, `EF_W_FACILITY_OVERVIEW`, and `PUB_DIM_FACILITY` were confirmed live on 2026-09-30. Weighting type groups by reported CH4 is a documented proxy: raw counts across types (wells vs. plants) are not comparable.
**Alternatives rejected:** Equal weights (uninformative); waiting for state production databases (per-state scraping, deferred to refine pad counts); weighting type groups by facility count (mixes wells and sites).
**Docs updated:** `configs/strata.yaml` header; `data/README.md`; TDD §3.1 note already references [ghgrp]. Supersedes the "weights are PLACEHOLDER" clause of the entry "Default strata list and placeholder weights" above.
**Status:** active

## 2026-09-30 — Sensor library provenance scheme and POD parametrisation (Phase 2)
**Decision:** Every numeric block in a sensor YAML (POD, quantification, false positive, cost) carries `citations` and a `provenance.status` of `fitted` (from a blind-release table via `mrvsim.sensors.fit`), `summary` (derived from published summary statistics), or `assumption`. POD curves may be written as `(pod50_kg_h, pod90_kg_h)` and are converted to `(a, b)` at load time; quantification may be written as a 95 % ratio interval and converted to `(β, σ)`. The loader rejects Tier B–D sensors with `enabled_for_certification: true` (PRD N2) and any block without citations (PRD N1). The POD fitter uses a weak N(0, 10²) ridge on (a, b) so tables with complete separation still fit; the ridge is recorded in the fit record. Sensors with `ab_cov` propagate POD-parameter uncertainty (TDD §4.1 satellite note); until fits exist the satellite covariances are labelled ASSUMPTION.
**Reason:** No blind-release tables were available inside the build environment, so all 13 sensor files ship with `summary`/`assumption` blocks and `TODO(verify)` markers rather than invented fits. The provenance field makes this visible to the attribution panel and gives a mechanical path to `fitted`.
**Alternatives rejected:** Hard-coding (a, b) without provenance (hides the gap); blocking Phase 2 until tables are transcribed (blocks Phases 3–8); dropping non-fitted sensors (leaves no sensor to simulate).
**Docs updated:** TDD §13 change log; REFERENCES adds `celestrak` (ok) and `cost-assumptions` (verify). `configs/sensors/_README.md` states the status.
**Status:** active
