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

## 2026-09-30 — Phase 3 observation-simulator conventions and two POD deviations
**Decision:**
1. **Wind floor in the POD wind term.** q_eff = q·(u_ref/max(u, u_min))^γ with u_min = 2 m/s (sensor YAML `pod.u_min_m_s`, default 2.0). Deviation from TDD §4.1, which has no floor.
2. **Satellite POD slopes.** Satellite curves use POD90/POD50 = 2 (steep) rather than ~3; TROPOMI POD50/POD90 = 2.5/5 t/h at 3 m/s.
3. **Pinned TLEs.** Runs propagate the TLE snapshot in `data/fitted/tle/` (CelesTrak, 2026-09-30) over the simulated year; pass times are representative, not historical. Cache key covers TLE text, facility coordinates, swath, step.
4. **Gate conventions.** Aircraft cloud gate: blocked with probability p_cloud·(1 − max_cloud_fraction_s). CMS outage and wind-sector gates are independent Bernoulli draws per hour. Tasked satellites keep one overpass per equal slice of the year up to `frequency_per_year`. Campaign visits are stratified over the year at local daytime hours.
5. **False-call rates.** A false positive reports a rate at the POD quantile drawn uniformly in [0.05, `reported_rate_quantile_max`].
6. **LDAR-Sim reuse (PRD Q6, provisional).** The observation simulator is native, vectorised numpy as the TDD's conventions require; LDAR-Sim remains vendored for cross-checks and tooling. Pending Nidhi's confirmation.
**Reason:** (1) and (2) were forced by the Phase 3 acceptance tests: without a wind floor, a 1 m/s draw triples q_eff and a shallow logistic gives a few-percent POD at 5 kg/h for Tanager-1, contradicting "never detected"; blind tests report no satellite detections well below the limit, which a steep slope reproduces. (1) is also physical: retrievals do not keep improving as wind drops toward zero. (3)–(5) are the simplest realisations of TDD §5.1–5.3 and §4.4; the "looser threshold" for aircraft cloud is not quantified in the TDD.
**Alternatives rejected:** No wind floor with per-sensor slope tuning only (still ~1 % POD at extreme low wind); historical TLEs from Space-Track (credentials); a diurnal wind-sector model for CMS (version 2).
**Docs updated:** TDD §4.1 (wind floor note), §5.2 (gate conventions note), §13; PRD §10 Q6 row (provisional answer).
**Status:** active

## 2026-09-30 — Estimand: posterior predictive of the realised annual mass (Phase 4)
**Decision:** The reported posterior for KPI-2 is the predictive distribution of the *realised* annual mass M_i = Σ_t Q_i(t), not the process expectation Σ_j ω_j π_j q_j T written in TDD §6.7. For each posterior draw, each intermittent source's annual on-hours H_j is drawn from a lognormal with mean π_j T and variance T[(1−π_j)² Var D_on + π_j² Var D_off]/(E D_on + E D_off) (renewal-reward CLT [renewal-theory]), clipped to [0, T]; M̂ = Σ_j ω_j q_j H_j. KPI-1 follows. `realised=False` recovers the TDD §6.7 expression.
**Reason:** TDD §2 defines true M_i from the realised state path and §7 scores calibration against it, but §6.7 integrates over the expected duty cycle only. A source with tens of events per year has a realised on-fraction that scatters 15–25 % around π with right skew, so when CMS pins π and q tightly the interval excludes the realised truth: on the default population coverage was 0.57 on CMS facilities and medians ran ~10 % above realised truth (median below mean of a skewed sum). The predictive restores the correspondence between the estimand and the scored quantity.
**Alternatives rejected:** Scoring against expected mass instead (would hide exactly the intermittency risk the KPI must express; a buyer pays for what was emitted); simulating the renewal process per posterior draw (10⁴ × 8760 per facility, unaffordable); Gaussian predictive (symmetric; leaves the median bias).
**Known caveat:** For CMS facilities the observed detected fraction already reflects the realisation, so adding predictive variance double-counts part of it (conservative). A version-2 CMS likelihood on the realised on-hours directly removes this.
**Docs updated:** TDD §6.7 (estimand note), §11 (new limitation 10), §13.
**Status:** active

## 2026-09-30 — Fast path: importance sampling with automatic tempered-SMC fallback (Phase 4)
**Decision:** The fast estimator draws 10⁴ prior samples and importance-weights them with the exact likelihood (TDD §6.7). When the effective sample size falls below 200, it reruns the facility with tempered sequential Monte Carlo (adaptive temperature ladder targeting conditional ESS = N/2, systematic resampling, two Metropolis rejuvenation moves per stage: a random walk on ln q and ν of included sources and a prior-independence redraw of one source or of K). Exact enumeration over 2^K states is grouped by drawn K (expected ~15 states per draw instead of 256). Snapshot non-detections are grouped by (sensor, 0.5 m/s wind bin), and non-detection factors whose maximal POD is below 10⁻⁵ are skipped. Survey detections use a Poisson-process association (sum over all maps of detections to candidate sources). CMS enters through a composite likelihood on three statistics of the usable-hour series: the detected-hour fraction (predicted 1 − Π_j(1 − ω_j π_j P_j) plus false calls; variance from the number of runs), the mean detected-run length (predicted from an OR-over-sources hazard: a run ends when the responsible source switches off or is missed *and* no other source is detected, in usable-hour units, mixed with false-call singleton runs), and the mean log reported rate (expected log of the detected *sum* by lognormal moment matching, mixed with the false-call rate distribution; σ = 0.9). Earlier drafts that averaged per-source miss rates and used the detectability-weighted *mean* rate gave CMS-only coverage of 0.45 with +15 % bias; the final form gives 0.905 with −10 % bias on the default population.
**Reason:** Plain importance sampling from the prior collapses on CMS-instrumented facilities (median ESS 7 of 10⁴); SMC restores ESS ≈ N. The padded 256-state enumeration cost 650 ms per facility; grouping cut it to ~50 ms. The remaining approximations are bounded (wind binning changes q_eff by < 8 % at 3 m/s; pruning error ≤ count × 10⁻⁵ in log-likelihood) and recorded here rather than hidden.
**Alternatives rejected:** Full HMM for CMS (version 2 per TDD §6.4); exact assignment sum for surveys (permanent over up to 8 candidates per detection set; version 2); PyMC for every facility (minutes per facility).
**Performance (default population, 7 sensors, 1e4 draws):** ~50–110 ms per facility, i.e. 340–700 s for 6,300 facilities, versus the TDD §10 budget of 20 s. **PRD N3 (< 60 s interactive recompute at R = 20) is not met by this implementation**; see the Phase 4 status note for the options (numba kernels, fewer draws, stratified subsampling for interactive runs, multiprocessing).
**Docs updated:** TDD §6.3 (grouping/pruning note), §6.4 (v1 composite likelihood definition), §6.5 (association rule), §6.7 (SMC fallback), §10 (measured timings), §13.
**Status:** active

## 2026-09-30 — The reported interval is the two-sided 90 % credible interval [p5, p95]; the certification bound stays the one-sided p90
**Decision:** The estimator reports the 5th, 10th, 50th, 90th, and 95th percentiles. The *interval* used for calibration κ (TDD §7), for the precision requirement w (PRD §5.4), and for the drill-down bar is [p5, p95]. The *certification bound* K̂_U,90 (PRD §5.3) remains the 90th percentile, i.e. "the true KPI lies below the bound with 90 % probability". PRD §5.4's formula is amended to w = (p95 − p5)/(2·p50); TDD §6.7's "10th, 50th, 90th" is superseded.
**Reason:** As written, TDD §6.7 and PRD §5.4 define the interval by the 10th and 90th percentiles, which is an 80 % credible interval, while §7 and the PRD call it a 90 % interval and require κ ∈ [0.85, 0.95]. The inconsistency surfaced in Phase 4 when a prior-only run (no sensors, posterior = prior = generating distribution) gave κ = 0.78 ≈ 0.80 instead of 0.90. Every downstream statement ("9 times out of 10", QUICKSTART) means the two-sided 90 % interval.
**Alternatives rejected:** Renaming the [p10, p90] interval an 80 % interval and moving the κ target to [0.75, 0.85] (contradicts the PRD's stated promise to buyers); using p95 as the certification bound (the PRD's logged decision rejected a 95 % bound for cost reasons).
**Docs updated:** PRD §5.4 (superseded formula + amended), PRD §11; TDD §6.7, §7 (width row), §13.
**Status:** active

## 2026-09-30 — Scoring conventions (Phase 5)
**Decision:**
1. **Three-state rule.** Certified: p90 ≤ B and w ≤ w_max (PRD §5.3–5.4). Fails: p10 > B, i.e. the KPI exceeds the bar with ≥ 90 % probability, the mirror image of the certification statement. Indeterminate otherwise. The two-sided [p5, p95] interval defines w; the one-sided 90 % bounds define the decisions.
2. **Completeness.** P̄_j = 1 − Π_k (1 − P_s(q_j, c_k)) over the facility's realised usable opportunities (snapshots and survey visits, with the wind and surface factors of each opportunity) and over usable CMS hours, evaluated at the source's own rate; the source's on/off state does not enter (completeness asks what the system *could* detect) [jacob2022]. Reported overall and per basin.
3. **Cost accounting.** Campaign/survey sensors: per-visit cost × visits scheduled (weather losses are paid for); tasked satellites: per-tasking × overpasses tasked; wall-to-wall satellites and CMS: per-site-year × facilities covered. "Detected mass" for cost per tonne is the realised annual mass of sources at facilities with at least one true detection (snapshot), or sources detected per se (survey/CMS). Costs are per sample unless scaled by a national facility count.
4. **Monte Carlo standard errors** are the standard deviation over replications divided by √R; with R = 1 the calibration SE falls back to the binomial SE over facilities.
5. **Interactive runs** may estimate a stratified facility subsample (`facilities_per_stratum`) with fewer draws and replications; shares use stratum weights so results are national estimates with larger SEs. (Proposed path to PRD N3; see Phase 4 status note.)
**Reason:** The PRD states the certification rule but not the failure rule or the operational definitions of completeness and cost; these are the simplest readings consistent with the text.
**Alternatives rejected:** Fails if p5 > B (asymmetric with the certification confidence); completeness from realised detections (conflates coverage with intermittency); charging only usable opportunities (understates cost).
**Docs updated:** TDD §7 (note), §13.
**Status:** active

## 2026-09-30 — Validation framework conventions (Phase 6)
**Decision:**
1. **Refusal rule.** Every V1–V7 module first checks the priors' provenance; with PLACEHOLDER priors it returns `status: skipped` with the reason and the pytest wrapper skips. `MRVSIM_ALLOW_PLACEHOLDER_VALIDATION=1` runs the logic anyway and flags the result `diagnostic_only` (never shown as a pass in the Validation panel).
2. **Targets file.** `data/fitted/validation_targets.yaml` holds every published comparison value with a citation key and a status (`ok` / `verify` / `missing`). Missing targets make the comparison `pass: null` and the test `skipped` when nothing remains to compare. Only the V3 intensities quoted in TDD §9 are transcribed (status `verify`); V1 quantiles, V2 basin totals, Cusworth persistence, the V3 boundary factor and the VLMR ratio are `missing` and must be transcribed from the papers.
3. **V1 fallback.** Without a published rate *sample*, the KS test is replaced by checking that each published quantile lies inside the simulated 90 % bootstrap band of that quantile (weaker; recorded in the result). The simulated "detected rate" is the facility total at one random daytime snapshot per facility, mirroring aerial survey censoring.
4. **V2 FEAST cross-check.** Steady sources are passed to FEAST as constant emissions; intermittent sources use FEAST's episodic components with duration E[D_on] and event rate 1/(E[D_on]+E[D_off]), so the 15 % criterion tests the agreement of the two intermittency models on annual mass. FEAST 3.1 (MIT, `vendor/feast`, branch FEAST_3.1) needs numpy aliases removed in numpy ≥ 1.24/2.0; `feast_adapter.import_feast` restores `np.bool`, `np.infty`, `np.math` before import.
5. **V6 estimator.** The satellite-only mass estimate is mean detected snapshot rate × known event duration (zero if no pass catches the event), averaged over random event start times; the test checks direction (ratio < 1) and the published order of magnitude.
6. **V4/V5/V7** are internal consistency tests and have no published targets; they are still refused on PLACEHOLDER priors per CLAUDE.md.
7. `mrvsim.validate.runner.run_all(quick=…)` writes `runs/validation/<timestamp>.json` for the Validation panel (PRD F14).
**Reason:** CLAUDE.md requires never marking a validation test passed against placeholders while still letting the machinery be exercised; the targets file makes the attribution of every compared number explicit (PRD G5).
**Docs updated:** TDD §9 note, §13; `vendor/README.md` (FEAST).
**Status:** active

## 2026-09-30 — Policy engine conventions (Phase 7)
**Decision:**
1. **Policy object.** `mrvsim.policy.Policy` holds per-sensor controls (enabled, coverage, coverage basis, frequency, targeting), the validation-tier filter, and a list of tip-and-cue `Rule`s; it serialises to YAML and converts to the `policy` config section used by the observation simulator.
2. **Rule evaluation is two-pass.** Triggering sensors (satellites, CMS) are simulated first; rules are evaluated day by day on their logs and schedule cued visits within N days (per-facility cooldown and cap); the full sensor set is then simulated with the cued visits merged. Because cued sensors never trigger rules in version 1 and every sensor's draws come from named seed streams, the triggering observations are identical in both passes and the result equals a single daily loop.
3. **Widest-interval allocation** is evaluated monthly with the fast estimator (IS, 1,000 draws) on the observations accumulated so far; the month's visit budget goes to the facilities with the widest relative interval on mass. It is 12 estimator passes and is meant for small populations or interactive exploration.
4. **Optimizer.** Optuna NSGA-II over per-sensor (enabled, coverage, frequency, targeting) with constraints κ ≥ κ_min and certified throughput share ≥ θ (`trial.set_constraint`), objectives (cost, w) minimised; trials at reduced R; the feasible non-dominated set is the Pareto frontier and the top-10 are re-scored at full R. Infeasible trials are kept in the record but excluded from the frontier.
**Reason:** Direct realisation of TDD §8.1–8.3 that reuses the simulator and pipeline without a per-day Python loop over facilities.
**Alternatives rejected:** A true daily event loop (per-facility Python loops, 365× the overhead, needed only when cued sensors can themselves trigger rules; version 2 if OGI→repair style chains are added); penalty-based single-objective optimisation (hides the trade-off the PRD wants to show).
**Docs updated:** TDD §8 note, §13.
**Status:** active

## 2026-09-30 — API and front-end conventions (Phase 8)
**Decision:**
1. **Endpoints.** `POST /api/run`, `GET /api/jobs/{id}`, `GET /api/run/{id}/summary|facilities|facility/{fid}|attribution`, `POST /api/run/{id}/facility/{fid}/budget`, `POST /api/run/{id}/tornado`, `GET /api/validation`, `POST /api/validation/run`, `POST /api/optimize`, `POST /api/gap-analysis`, `GET /api/quickstart`, `GET /api/references`, `GET /api/sensors`. Long operations run in a thread pool and return a job id (the PRD's `POST /run` is asynchronous because even interactive runs take a minute).
2. **Interactive defaults (PRD N3 strategy).** 10 facilities per stratum, 2,000 posterior draws, 3 replications, 30 generated facilities per stratum: ~1–2 minutes on this laptop. All adjustable under Sensors → Advanced. This is the proposed resolution of the N3 gap: an interactive recompute estimates a stratified subsample with stratum-weighted shares; batch quality needs the full sample and R = 200.
3. **Map recolouring is client-side.** The run persists p5, p10, p50, p90, p95 per facility and the browser applies the three-state rule at the user's (B, w_max) without a rerun; the rule mirrors `mrvsim.score.metrics.classify`.
4. **Tornado chart** is computed on demand (one pipeline run per one-at-a-time change: each deployed sensor off, doubled frequency, full coverage; each absent core sensor added at 20 %) and cached in the run directory.
5. **Gap analysis** goes through the API server, which holds the Anthropic credentials (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or an `ant auth login` profile); the browser never sees a key and no key is in code. Model `claude-opus-5-5` with the server-side refusal fallback; the system prompt carries the run's headline, slices, and tornado; a fenced YAML policy in the reply is parsed and offered with an **Apply** button. The browser-direct pattern from the artifacts documentation was not used because this is a self-hosted app, not a claude.ai artifact.
6. **Basemap**: OpenStreetMap raster tiles (attribution shown); facility coordinates are synthetic.
7. **Colours** follow the dataviz reference palette: status good/critical for certified/fails, a neutral for indeterminate, categorical slots for sensors, with text labels and legends so colour is never the only encoding.
**Reason:** Direct realisation of PRD F9–F14 and CLAUDE.md Phase 8 within the measured estimator speed.
**Docs updated:** `docs/QUICKSTART.md` rewritten against the actual controls; PRD §10 Q6 unchanged; README run instructions.
**Status:** active

## 2026-10-01 — Quick and Full compute modes; facility coordinates fixed across replications
**Decision:** `POST /api/run` takes `mode: quick | full | custom`. Quick = 10 facilities per stratum, 2,000 posterior draws, 3 replications, 30 generated facilities per stratum (the PRD N3 interactive target, ~1–2 min). Full = every facility of the default sample (100 per stratum), 10,000 draws, 5 replications (~40 min on this laptop; TDD resolution). The UI shows a time estimate from measured per-facility costs and offers "Re-run this mix at full resolution" from any quick run. Facility coordinates are now drawn from the run-level seed tree, so locations are identical across Monte Carlo replications and the satellite overpass cache is reused after the first replication; emissions, states, conditions, and observations still vary per replication.
**Reason:** Nidhi's request to explore quickly and confirm at high resolution; replication-dependent coordinates were recomputing ~100 s of overpasses per replication for no modelling benefit (the sample sites are fixed; their emissions are the random quantity).
**Docs updated:** QUICKSTART (compute mode), README; PRD N3 interpretation (interactive = stratified subsample) now stated in the UI.
**Status:** active

## 2026-10-01 — Literature pass: sensor parameters grounded in published blind tests; TROPOMI acceptance test relaxed
**Decision:** Every sensor YAML block that was a recalled "summary" now cites a fetched source with the exact published statistic in its `provenance.basis`, and the corresponding REFERENCES entries carry confirmed DOIs (status `ok`). Key values: Bridger GML POD90 = 3 kg/h, ~1 kg/h sensitivity [johnson2021], 95 % error −64.1 %/+87.0 % [daniels2023]; Insight M (Kairos) all releases > 5 kg/h per m/s detected, smallest 3.40, largest missed 10.47 kg/h, slope 1.13 [elabbadi2024]; Carbon Mapper largest missed 6.61 / smallest detected 10.92 kg/h [elabbadi2024]; new `ghgsat_av` aircraft sensor (2.91 detected; false negatives at 15–30 kg/h); GHGSat-C2 smallest satellite detection 0.197 t/h, Sentinel-2 1.4 t/h by 3 of 4 teams, pooled satellite error −68 %/+110 %, no false positives [sherwin2023]; CMS DL90 2.7–5.9 kg/h (6 of 8) [bell2023] and 3.9–6.2 kg/h (best 4) with 6.9–13.2 % false-positive reports [ilonze2024]; OGI 90 % POD at 7 scfh = 0.134 kg/h for experienced surveyors, 4.1 % false positives on zero pads [zimmerle2020]; **TROPOMI detection limit "down to ~5 t/h under favourable conditions", detected-plume 5th percentile 8 t/h [schuit2023] → POD50/POD90 = 6/10 t/h.** No block is yet `fitted` to a release table (none of the papers' per-release tables were extracted).
**Consequence for CLAUDE.md Phase 3 acceptance test:** "a 10 t/h steady source is detected by TROPOMI on nearly every clear overpass" is not supported by Schuit 2023; at 10 t/h the literature-consistent curve gives ~65–90 % depending on wind. The test now requires detection on more than half of clear overpasses. **Nidhi to confirm** this change to the CLAUDE.md criterion.
**Not confirmed:** `chen2024` (Bridger blind test) could not be located and is superseded by [johnson2021]; the Permian 4.6 % [4.4, 4.9] intensity in TDD §9 is not in the Hajny et al. preprint abstract and is excluded from V3 scoring until a source is found; Ravikumar et al. is a 2019 paper (key kept); Zimmerle 2020 is in EST, not JAWMA.
**Docs updated:** `configs/sensors/*.yaml`, `docs/REFERENCES.md`, `data/fitted/validation_targets.yaml` (Cusworth 2022 persistence 0.26 Permian / 0.60 Marcellus, point-source share ~40 %, duration shares; Sherwin 2024 loss-rate range 0.75–9.63 %, weighted 2.95 %; Omara national site-level quantiles from Rutherford 2021's code), `tests/unit/test_observe.py`, `tests/unit/test_sensors.py`.
**Status:** active

## 2026-10-01 — Fitted emission priors from public data replace the placeholders
**Decision:** `configs/priors/fitted_2026-10-01.yaml` (provenance FITTED) is the default prior set; `placeholder_v0.yaml` is kept for tests. Fitting (`data/scripts/fit_priors.py`, provenance in `data/fitted/priors_fit_provenance.json`):
1. **Steady sources** — lognormal fixed by moment-matching the Rutherford et al. 2021 component database (3,082 emitting components, mean 10.9 and median 1.23 kg CH₄/d): μ₀ = ln(0.0513 kg/h) = −2.97, σ₀ = 2.09 [rutherford2021].
2. **National well-pad fit** — λ (sources per site), p (intermittent share), μ₁, σ₁, q_tail, α and ν_off are fitted by Nelder-Mead so that the generator's site-level annual-mean rate reproduces the Omara et al. 2018 national site distribution as reproduced in Rutherford's code (498,121 sites: p25 0.048, p50 0.325, p90 4.09, p99 15.8 kg/h; mean 1.67; top-1 % share 25 %) and the Cusworth 2022 snapshot share of flux from sources > 10 kg/h (~40 %) [rutherford2021; cusworth2022].
3. **Per-basin well-pad fits** (Permian, Appalachian, DJ, Uinta; Fort Worth → `other`) — λ, p, μ₁, σ₁, q_tail, α and a shift of μ₀ are fitted to the site-level survival curves of the Sherwin et al. 2024 correction release (`CDF_data`, "fraction of sites emitting at or above" 0.1–1,000 kg/h; aerial above the campaign transition point, Rutherford BASE below) [sherwin2024]. Cusworth persistence (Permian 0.26, Marcellus 0.60) may move p within ±50 % of the survival fit only when it costs less than 15 % of the survival fit; otherwise it is reported as a diagnostic.
4. **Midstream** — λ and μ₀ for gathering, processing, transmission, storage set so the facility mean matches GHGRP RY2023 reported CH₄ per facility (metric tons CH₄; processing median 10.5, transmission 12.6, storage 17.1 kg/h; gathering 61 kg/h but those are operator-basin aggregates) [ghgrp]; p_intermittent = 0.15 assumed.
5. **Not fitted** — durations beyond ν_off (ν_on fixed at ln 7.4 h; τ, σ_ν placeholders), throughput distributions, observing conditions. Haynesville, Eagle Ford, Bakken, Anadarko, San Juan inherit the national well-pad fit.
**Reason:** Nidhi asked for real numbers; these are the public datasets reachable from this environment (Zenodo packages for Rutherford 2021 and the Sherwin 2024 correction; Envirofacts). The Omara distribution is model-derived (regression on production) and GHGRP is bottom-up (under-reports ~2× against aerial measurements per Sherwin 2024), so the priors are grounded but not direct measurements.
**Model limitation found:** a single intermittent share per stratum (TDD §3.3) cannot reproduce both a basin's high fraction of low-level emitters and a low persistence of its large sources (Permian); a size-dependent intermittency probability is a version-2 item.
**Effect:** placeholder priors had a median well-pad rate ~20× too high; with fitted priors the default-policy calibration is 0.92 (mass) and 0.91 (intensity), well-pad intervals are wide (w ≈ 3.5: two aircraft passes say little about a 0.3 kg/h site) and midstream intervals are narrow (w ≈ 0.25–0.5).
**Docs updated:** TDD §3.2–3.4 (fitted values referenced), `data/fitted/validation_targets.yaml` (Sherwin Tables S10/S21 per basin, survival curves, detected-site quantiles; Cusworth persistence and shares; Omara quantiles), `mrvsim/validate/v1_rates.py` (survival-curve and Cusworth-style persistence checks), `v2_totals.py` (basin loss rates vs Table S10), `tests/`.
**Status:** active

## 2026-10-01 — Certification is the compliance decision at 95 %; precision and evidence are reported, not vetoes
**Decision:** The three-state rule is a decision on the bar only: **certified** iff $\hat{K}_{95} \le B$ (the KPI is below the bar with at least 95 % posterior probability); **fails** iff $\hat{K}_{5} > B$ (above the bar with at least 95 %); **indeterminate** iff the 90 % credible interval straddles $B$. The relative half-width $w = (\hat{K}_{95} - \hat{K}_{5}) / (2\hat{K}_{50})$ no longer blocks certification. It is reported as a **precision grade** ("precise" iff $w \le w_{\max}$), stays the optimizer objective and Pareto axis, and $w_{\max}$ stays a user control for the grade and for optimizer feasibility. A per-facility **evidence** measure is added (PRD §5.4a): the posterior interval's log width $\ln(\hat{K}_{95}/\hat{K}_{5})$ as a fraction of the prior's (the prior being the same draws unweighted, i.e. the posterior with no observations), plus the counts of usable snapshots, survey visits and CMS hours. A certification is flagged **prior-only** when that ratio exceeds `scoring.prior_only_ratio` (default 0.9) or when nothing usable was observed. The drill-down states $P(K \le B \mid \text{data})$ directly, interpolated on a stored 101-point posterior quantile grid.
**Reason:** Nidhi's use case is bar compliance: "we care that the maximum plausible emission rate is below our bar within an error margin". That statement is PRD §5.3's one-sided bound, with the error margin being the tail probability, not the interval width. The width test of §5.4 penalised facilities whose whole interval lies under the bar (Facility 1201: [4.3 %, 12.8 %] against a 15 % bar, grey because $w = 0.58$); it is measured relative to the facility's own median, so a small emitter far below the bar fails it although its uncertainty cannot change the decision. The width test's real job was to stop the population prior from certifying facilities on its own; the evidence ratio measures that directly, and the "prior-only" flag makes it visible without conflating it with the decision. The level moves from 90 % to 95 % at Nidhi's choice (stricter at the bar; the earlier cost argument is outweighed by the credibility of a 95 % statement to buyers).
**Alternatives rejected:** keeping $w \le w_{\max}$ as a certification condition (mislabels clear passes as undecided); a bar-relative width veto $(\hat{K}_{95} - \hat{K}_{5})/(2B)$ (still a veto, and ad hoc); a minimum-evidence veto (would block certifications that are correct under the prior; shown and flagged instead); a user-selectable confidence level (one level keeps "certified" meaning one thing); an absolute-width evidence ratio (fails when the data move the posterior into the prior's tail: the 20-pass aircraft test gave 0.84 in absolute terms and 0.09 in log terms).
**Supersedes:** 2026-09-30 "Certification rule uses the 90 % upper credible bound and a precision requirement"; the rejection of a 95 % bound in 2026-09-30 "The reported interval is the two-sided 90 % credible interval"; item 1 of 2026-09-30 "Scoring conventions" and its rejection of "fails if p5 > B"; item 3 of "API and front-end conventions" (client-side recolouring now applies the decision at $B$ and the precision grade at $w_{\max}$ separately).
**Effect (default sensor mix, quick mode, seed 20260930, bar 0.2 % intensity):** certified share of facilities 0 % → 27 % (throughput-weighted 0 % → 31 %); indeterminate 53 % → 45 %; fails 47 % → 28 %; of the certified facilities 9 % are also precise at $w_{\max} = 0.3$ and 1.4 % are prior-only; κ unchanged (0.90). Validation V4's certified-share stability check keeps its definition but now measures the decision-only share.
**Docs updated:** PRD §5.3, §5.4 (superseded as a condition), new §5.4a, §11; TDD §6.7 (quantile grid and prior summary persisted), §7 (table rows), §8.3 (note), §13; QUICKSTART (colours, drill-down, conventions); `configs/default.yaml` (`prior_only_ratio`).
**Status:** active

## 2026-10-01 — Regional flight campaigns; throughput targeting is a top-k cutoff; the drill-down shows the sensor assignment
**Decision:** (1) Campaign and survey sensors take `scheduling: independent | campaign` and `campaign_days` (default 5). Under `campaign`, each basin with covered facilities is flown in one window of `campaign_days` consecutive days per equal slice of the year (`frequency_per_year` slices); every covered facility in the basin is visited once per window on a uniform day inside it. `independent` keeps the previous per-facility random dates and stays the default for existing configs; the UI default policy sets `campaign` for `bridger_gml`. (2) `targeting: throughput` is, and was, a strict top-k by marketed gas; the UI, QUICKSTART and TDD §8.1 said "throughput-weighted", which is now corrected to "top facilities by throughput". (3) The facility drill-down lists every sensor in the policy with its rule, whether this facility is covered and why (rank among all generated facilities for throughput targeting), its planned visit days, and the facility's gas-throughput rank.
**Reason:** Nidhi clicked three neighbouring sites and asked why only one had GHGSat. Selection is per facility by coverage share and targeting, with no geography, so neighbours need not share sensors, and aircraft dates were drawn independently per facility, so neighbours were never flown on the same day, unlike real campaigns. The assignment was invisible in the UI. The green site ranked 290 of 1,890 by throughput and so got GHGSat (top 30 %) and a continuous monitor (top 20 %); its neighbours ranked 743 and 989.
**Alternatives rejected:** routing within a window by longitude (adds realism the quantification model does not use); a per-sensor sites-per-day capacity from the sensor YAML (no citable number; `campaign_days` is the user's knob instead); weighted sampling for `throughput` targeting (would change results; the cutoff is what operators mean by "survey the biggest sites"); confining `other`-basin coordinates outside named basins (Nidhi deferred).
**Limitation recorded:** cloud and wind are still drawn per facility per hour; a shared campaign day does not yet share weather or quantification error (TDD §4.2 "correlated error, version 2"). Campaign windows therefore change *when* neighbours are seen, not yet how correlated their errors are.
**Docs updated:** TDD §5.1 (aircraft bullet), §8.1 (Targeting row corrected, Scheduling row added), §13; QUICKSTART steps 3 and 5; gap-analysis system prompt.
**Status:** active

## 2026-10-01 — Incidental capture: tasked scenes and survey blocks observe the neighbours inside them; tasked satellites use their field of regard
**Decision:** (1) Each tasked imager's YAML carries its scene size (`fov_along_km`, `fov_cross_km`) and maximum off-nadir pointing (`max_off_nadir_deg`); aircraft carry a surveyed block size (`footprint.survey_block_km`). (2) A tasked satellite can be tasked on any pass whose ground track lies within swath/2 plus the pointing reach of the facility, with the reach from the spherical-Earth geometry at the altitude derived from the pinned TLE's mean motion. Previously the band was the 12 km scene itself, which treated GHGSat as a nadir-only instrument and left most monthly tasking slices without a usable pass. (3) When a facility is tasked or surveyed, every facility inside the scene framed on it (a rectangle aligned with the ground-track heading at closest approach for satellites; an axis-aligned block for aircraft) gets an incidental snapshot at the same hour, sharing the target's cloud state, with its own sun angle, wind and detection draw. Incidental rows are flagged in the log with the target's index, cost nothing, and feed the estimator, the evidence counts and completeness exactly like intended observations. A (facility, hour) pair is logged once. A policy flag `incidental_capture` (default on) disables the mechanism. The drill-down timeline shows incidental looks in lighter colours with the framing facility in the tooltip, and the monitoring table counts them for sensors that did not select the facility.
**Values:** GHGSat-C1: FOV ≈ 12 × 12 km, pointing ±15° along and cross-track, 500 km SSO (ESA/CSA product specification [ghgsat-c1-spec], status summary). Tanager-1: nadir swath 18.6 km growing to 24.2 km with look angle [tanager-spec]; the maximum look angle (29°) is *derived* from that growth and the along-track scene length is assumed equal to the swath, both status assumption/verify. PRISMA: 30 × 30 km, ±14.7° [eoportal-prisma]; EnMAP: 30 × 30 km, ±30° [enmap-mission]; both verify. Bridger GML survey block 0.5 km: analyst assumption [survey-footprint-assumption], verify; Kairos and AVIRIS-NG/GAO have no footprint yet (no incidental capture) pending flight-plan data. Wall-to-wall instruments (TROPOMI, Sentinel-2, Landsat) are unchanged.
**Reason:** Nidhi: a satellite tasked on a high-throughput site "should see incidental neighbours", following the physics of what the instrument's field of view captures and how it can point. The previous code could not represent either fact: tasking opportunities were nadir-only and a scene observed exactly one facility.
**Alternatives rejected:** a circular footprint (wrong shape for push-broom strips); ignoring heading (a 12 km square is nearly rotation-invariant but Tanager/PRISMA strips are not); charging incidental rows as taskings (the operator pays per scene); treating incidental non-detections as weaker evidence (the POD is the same; a scene is a scene); modelling the along-track ±15° pointing (changes timing by seconds, not which passes qualify); correcting the footprint for off-nadir elongation (≤ 4 % at 15°).
**Limitations recorded:** cloud is shared within a scene but wind and quantification error are still independent per facility (TDD §4.2 version-2 item); scenes are centred exactly on the target (operators may frame to cover several sites at once, which would increase incidental coverage); the Tanager pointing angle and the aircraft block are assumptions flagged in the attribution panel.
**Docs updated:** TDD §4 table, §5.1, §13; REFERENCES (ghgsat-c1-spec, tanager-spec, eoportal-prisma, enmap-mission, survey-footprint-assumption); sensor YAMLs (ghgsat_c, tanager1, prisma, enmap, bridger_gml); QUICKSTART; status note.
**Status:** active
