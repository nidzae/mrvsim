# Product Requirements Document: Methane MRV Coverage Simulator (MRVSim)

| Field | Value |
|---|---|
| Status | Draft v0.1 |
| Owner | Nidhi |
| Last updated | 2026-09-30 |
| Companion documents | `TECHNICAL_DESIGN.md`, `CLAUDE.md`, `QUICKSTART.md`, `REFERENCES.md`, `DECISION_LOG.md` |

This document is the source of truth for **what** MRVSim is and **why**. `TECHNICAL_DESIGN.md` is the source of truth for **how** it is computed. When the two disagree, fix the disagreement in the same change; do not let them drift.

---

## 1. Summary

MRVSim is a simulation tool that answers one question: **for a given mix of methane sensors deployed over US onshore oil and gas facilities, how tightly can we bound each facility's annual methane emissions, and at what cost?**

It does this with an Observing System Simulation Experiment (OSSE): it generates a synthetic population of facilities with known ("true") emissions, simulates what each sensor would observe over a year, runs a statistical estimator on those simulated observations, and scores the estimator's output against the known truth.

The output is two facility-level KPIs, each with a statistically calibrated uncertainty interval:

1. **Methane intensity** (methane emitted per unit of marketed methane), for gas-producing facilities.
2. **Absolute methane emissions** (tonnes CH₄ per year), for every facility.

The tool lets a user change which sensors are deployed, where, and how often, and immediately see the effect on both KPIs, on the share of facilities that can be certified against a chosen bar, and on cost.

---

## 2. Problem statement

### 2.1 The market problem

Buyers of natural gas (hyperscale data center operators, EU importers under the EU Methane Regulation, utilities) increasingly want to buy gas with a verified low methane footprint. Producers want to sell it at a premium. Certification schemes exist (MiQ, Project Canary TrustWell, Equitable Origin EO100, GTI Veritas), but none of them state a facility's emissions as **a number with a rigorously derived error bar**. A buyer cannot currently tell the difference between a facility that is measured well and one that is measured badly, if both report the same point estimate.

### 2.2 The technical problem

Methane emissions from oil and gas are:

- **Heavy-tailed.** A small fraction of sources emit most of the methane.
- **Intermittent.** Many large sources are on for a small fraction of the time.
- **Below the detection limit of most remote sensors most of the time.**

Because of these three properties, no single sensor class can measure a facility's annual emissions. Satellites see only the largest plumes, only on clear days, only at one time of day. Aircraft see more but visit rarely. Continuous ground monitors see timing but quantify poorly. Any honest annual number has to combine sensors and has to state how uncertain it is.

### 2.3 What is missing

Nobody has published, for the US onshore oil and gas sector, a tool that:

- represents the full emissions population (steady and intermittent, above and below detection limits),
- represents every sensor class with its validated probability-of-detection curve,
- combines their observations in one statistical model that produces a calibrated interval,
- lets a user change the sensor mix and see the result,
- and reports the cost of each configuration.

Existing open-source simulators (LDAR-Sim, FEAST) model leak detection and repair programs. They are the right foundation, but they do not estimate an annual emissions number with an uncertainty interval, which is the quantity buyers and regulators need.

---

## 3. Goals and non-goals

### 3.1 Goals

| # | Goal | Success measure |
|---|---|---|
| G1 | Produce, per facility, a calibrated 90% uncertainty interval on annual methane intensity and on absolute annual emissions | Calibration score ≥ 0.85 and ≤ 0.95 on synthetic truth (see §6) |
| G2 | Let a user change sensor deployment (which, where, how often) and see the effect on both KPIs, certifiability, and cost | Full recompute of a stratified US sample in under 60 s on a laptop |
| G3 | Reproduce published basin-level results before making new claims | Passes validation tests V1–V6 in `TECHNICAL_DESIGN.md` §9 |
| G4 | Identify the cheapest sensor mix that meets a chosen (bar, precision) target | Pareto frontier chart of cost vs. achievable precision |
| G5 | Attribute every dataset, curve, and method to its source, in the tool and in the documents | Every model parameter traces to a `REFERENCES.md` key |
| G6 | Be usable by a non-data-scientist | Quick-start guide reachable in one click from any screen |

### 3.2 Non-goals for this version (backlog)

Listed in the order they were deferred. Each has a placeholder in `DECISION_LOG.md`.

1. Agriculture and waste sectors
2. Reporting and Verification layers (repair verification, action loop, audit trail)
3. Cost-driven ranking as a hard constraint (cost is computed and displayed; it does not yet filter configurations)
4. Offshore facilities
5. Non-US geographies
6. Coal mines
7. Asset ownership mapping (the tool attributes to coordinates only)
8. Data access, governance, business model
9. Comparison against current regulatory requirements (EU Methane Regulation, EPA OOOOb, OGMP 2.0)
10. 3D plume dispersion modeling (replaced by empirical probability-of-detection curves; see §7.3)

---

## 4. Users

| User | What they need from the tool |
|---|---|
| **Analyst (primary, Nidhi)** | Explore sensor mixes; produce the Pareto frontier; generate evidence for a proposed KPI bar |
| **Gas buyer / certifier** | Look up a facility coordinate and see whether it meets a bar, with the interval |
| **Regulator / standards body** | See what precision is achievable with validated technology today, at what cost |
| **Researcher / reviewer** | Inspect the model, the priors, the validation tests, and the attribution chain |
| **Claude Code (builder)** | Read this PRD and the design doc; update them when implementation decisions change them |

---

## 5. The KPIs

### 5.1 KPI-1: Methane intensity (loss rate)

Defined as in the Natural Gas Sustainability Initiative (NGSI) protocol, which MiQ, ONE Future, and GTI Veritas also use [REF: ngsi; methane-by-the-numbers].

$$
I = \frac{M_{\text{CH}_4,\text{gas}}}{V_{\text{gas,mkt}} \cdot \rho_{\text{CH}_4} \cdot X_{\text{CH}_4}}
$$

where:

- $M_{\text{CH}_4,\text{gas}}$ = mass of methane emitted over the reporting year, allocated to natural gas production (kg)
- $V_{\text{gas,mkt}}$ = volume of natural gas marketed over the year (standard m³)
- $\rho_{\text{CH}_4}$ = density of methane at standard conditions (0.678 kg/m³ at 15.6 °C, 1 atm)
- $X_{\text{CH}_4}$ = mole fraction of methane in the marketed gas (dimensionless, typically 0.80–0.95)

For facilities that produce both oil and gas, emissions are allocated to gas by energy share:

$$
f_{\text{gas}} = \frac{E_{\text{gas}}}{E_{\text{gas}} + E_{\text{oil}}}, \qquad M_{\text{CH}_4,\text{gas}} = f_{\text{gas}} \cdot M_{\text{CH}_4,\text{total}}
$$

where $E$ is the energy content of each product (MJ). This is a convention, not a physical quantity, and it is one of the reasons KPI-2 exists.

### 5.2 KPI-2: Absolute annual methane emissions

$$
M_{\text{CH}_4,\text{total}} = \sum_{j=1}^{K} \pi_j \, q_j \, T
$$

where:

- $K$ = number of emission sources at the facility (including ones no sensor detected)
- $\pi_j$ = duty cycle of source $j$: the fraction of the year it is emitting (0 to 1)
- $q_j$ = emission rate of source $j$ when it is emitting (kg/h)
- $T$ = 8,760 hours

Units: kg/yr, reported in tonnes/yr.

### 5.3 The certification statement

Both KPIs are reported as a posterior distribution (see §8). The certification rule uses the **upper 90% credible bound**:

$$
\text{Certified at bar } B \iff \hat{K}_{U,90} \le B
$$

where $\hat{K}_{U,90}$ is the value below which the true KPI lies with 90% probability, given the observations. The bar $B$ is set by the user (e.g., 0.2% intensity, or 50 t/yr absolute).

**Amended (2026-10-01, DECISION_LOG "certification is the compliance decision at 95 %"):** the certification bound is the **upper 95% credible bound**, and the failure statement is its mirror image:

$$
\text{Certified at bar } B \iff \hat{K}_{95} \le B, \qquad \text{Fails} \iff \hat{K}_{5} > B, \qquad \text{Indeterminate otherwise.}
$$

Equivalently, with $p = P(K \le B \mid \text{observations})$: certified iff $p \ge 0.95$, fails iff $p \le 0.05$. The tool reports $p$ itself for every facility. The 90% bound $\hat{K}_{U,90}$ above is superseded.

### 5.4 The precision requirement

A bar is a pair $(B, w)$, where $w$ is the maximum permitted relative half-width of the 90% credible interval:

$$
w = \frac{\hat{K}_{U,90} - \hat{K}_{L,10}}{2 \, \hat{K}_{\text{median}}} \qquad \text{(superseded 2026-09-30; see DECISION\_LOG "two-sided 90 % credible interval")}
$$

**Amended (2026-09-30):** the 90% credible interval is two-sided, $[\hat{K}_{5}, \hat{K}_{95}]$, and

$$
w = \frac{\hat{K}_{95} - \hat{K}_{5}}{2 \, \hat{K}_{\text{median}}}.
$$

The certification bound in §5.3, $\hat{K}_{U,90}$, is unchanged: it is the one-sided 90% upper bound (90th percentile). The original formula above used the 10th and 90th percentiles, which bound only 80% of the posterior.

A facility whose interval is wider than $w$ is reported as **indeterminate**, not as passing or failing. *(Superseded 2026-10-01; see below.)*

**Amended (2026-10-01, DECISION_LOG "certification is the compliance decision at 95 %"):** $w$ is a **precision attribute**, not a condition of certification. A facility is **precise** when $w \le w_{\max}$ and **wide** otherwise; both can be certified. The map draws wide facilities with a dark ring and the drill-down reports $w$ next to the decision. $w$ remains the metric the optimizer minimises (§8 of the TDD) and the axis of the Pareto frontier. Reason: $w$ is relative to the facility's own median, so a small emitter whose entire interval lies under the bar would otherwise be called indeterminate although the decision is not in doubt.

### 5.4a The evidence requirement

A certification should rest on measurements of the facility, not on the population prior alone. For each facility the tool reports:

- the number of usable snapshots, survey visits and continuous-monitor hours the estimator saw;
- the **evidence ratio** $e = \ln(\hat{K}_{95}/\hat{K}_{5}) \,/\, \ln(\hat{K}^{\text{prior}}_{95}/\hat{K}^{\text{prior}}_{5})$, the posterior interval's log width as a fraction of the prior's, where the prior interval is the estimator's posterior with no observations;
- a **prior-only** flag when $e > 0.9$ (configurable) or when no usable observation exists.

Prior-only certifications are counted separately in every certified-share metric and drawn faded on the map. They are not withheld: under a calibrated prior the statement is still true, but a buyer should see that it rests on the population, not on the site.

### 5.5 Secondary metric: observing system completeness

Defined by Jacob et al. (2022) [REF: jacob2022] as the fraction of total point-source emissions above 10 kg/h in a domain and time window that a given instrument or constellation can detect:

$$
C = \frac{\sum_j q_j \, \pi_j \, T \cdot \mathbb{1}[q_j > 10] \cdot \bar{P}_j}{\sum_j q_j \, \pi_j \, T \cdot \mathbb{1}[q_j > 10]}
$$

where $\bar{P}_j$ is the probability that source $j$ is detected at least once during the year by the deployed system.

### 5.6 Cost metrics

$$
\text{Cost per tonne detected} = \frac{\text{Annual monitoring cost}}{\sum_j \text{detected mass}_j}, \qquad
\text{Cost per certified MMBtu} = \frac{\text{Annual monitoring cost}}{\text{MMBtu marketed from certified facilities}}
$$

---

## 6. Requirements

### 6.1 Functional

| ID | Requirement |
|---|---|
| F1 | Generate a stratified synthetic population of US onshore oil and gas facilities with known emissions (rate, duty cycle, source count) calibrated to published measurements |
| F2 | Represent each sensor class with a validated probability-of-detection curve, a quantification-error distribution, a false-positive rate, operating constraints, and a cost, each attributed to a source |
| F3 | Generate observation opportunities per sensor per facility over a simulated year, gated by orbit, clouds, sun angle, wind, and the deployment policy |
| F4 | Run an estimator that turns the observation log into posterior distributions on KPI-1 and KPI-2 per facility |
| F5 | Score each configuration: calibration, interval width, certifiable share of facilities and of throughput, completeness, cost |
| F6 | Let the user set, per sensor class: on/off, coverage fraction, frequency, targeting policy, validation-tier filter |
| F7 | Support tip-and-cue rules (one sensor's detection triggers another's deployment) |
| F8 | Produce a Pareto frontier of cost vs. achievable precision for a chosen bar |
| F9 | Facility map with three-state coloring (certified / fails / indeterminate) and a per-facility drill-down showing the interval, the observation timeline, and the variance budget |
| F10 | Portfolio dashboard with headline metrics, tornado chart, and slices by basin, facility type, and rate bin |
| F11 | A gap-analysis chat panel where the user asks Claude to interpret the current run and propose a configuration, applied with one click |
| F12 | A quick-start guide reachable from every screen |
| F13 | An attribution panel listing every dataset, curve, and method used in the current run, with citation keys resolving to `REFERENCES.md` |
| F14 | Run the six validation tests in `TECHNICAL_DESIGN.md` §9 and display pass/fail |

### 6.2 Non-functional

| ID | Requirement |
|---|---|
| N1 | Every model parameter in the codebase carries a citation key in its docstring or config entry |
| N2 | Sensors without published blind-test performance data are excluded from certifiable configurations by default and visually flagged |
| N3 | Full recompute for the default stratified sample completes in under 60 s on a laptop; posterior computation may use a fast approximation with the exact method reserved for validation runs |
| N4 | All randomness is seeded; any run is reproducible from its config and seed |
| N5 | Open-source dependencies only; the simulation engine forks LDAR-Sim (MIT) |

---

## 7. Discussion: decisions and their reasoning

Each item here is also logged in `DECISION_LOG.md` with a date. New decisions go there first and are summarized here.

### 7.1 Why two KPIs, not intensity alone

Roughly half of global oil and gas methane comes from oil production rather than gas production; the IEA Global Methane Tracker attributes about 45 Mt/yr to oil operations against about 35 Mt/yr to natural gas operations [REF: iea-gmt-2026; verify current-year split]. Intensity on marketed methane has three failure modes for those emissions:

- A facility that produces oil and flares, vents, or reinjects its associated gas has little or no marketed gas. Its intensity is undefined or meaningless, however large its emissions.
- Energy allocation between oil and gas is a convention. Two auditors can allocate the same emissions differently and produce intensities that differ by a factor of two or more [REF: haynesville-2025].
- A buyer of gas from a mixed basin needs to know the basin's total methane burden, not only the share allocated to the molecules they bought.

Absolute emissions per facility have none of these problems. They are harder to compare across facilities of different size, which is why intensity is kept as KPI-1 for gas buyers. Both are computed from the same posterior, so there is no extra measurement cost.

A third KPI (methane per barrel of oil equivalent, covering both products) was considered and deferred. It would reintroduce an allocation convention. It can be added if oil buyers ask for it.

### 7.2 Why the KPI carries an error bar, not a grade

Existing certification grades (MiQ A–F) are assigned from a point estimate plus monitoring-tier requirements. Two facilities with the same point estimate but different measurement quality get the same grade. An interval separates them. It also makes the KPI falsifiable: if a certified facility is later measured outside its interval more often than the interval's confidence level implies, the method is wrong and can be shown to be wrong. That property is what will make researchers and regulators accept the number.

### 7.3 Why probability-of-detection curves instead of 3D plume simulation

A probability-of-detection (POD) curve says: for a source emitting at rate $q$ under conditions $c$, the sensor detects it with probability $P(q, c)$. These curves are measured empirically in single-blind controlled-release experiments, where a known amount of methane is released and the sensor operator does not know the rate [REF: sherwin2023; elabbadi2024; metec-aded]. The curve already contains the plume physics for the conditions tested.

Simulating the plume in 3D would require a dispersion model whose own errors would need validation, would slow the simulation by orders of magnitude, and would not add information beyond what the controlled releases already measured. The cost of the decision is that POD curves are only valid for conditions similar to the tests (flat terrain, bright surfaces, moderate wind). The design doc addresses this with a surface-adjustment factor carrying its own uncertainty (§7 of `TECHNICAL_DESIGN.md`).

### 7.4 Why a two-parameter source model (duty cycle × rate)

A single snapshot cannot distinguish a steady 500 kg/h source from a 5,000 kg/h source that is on 10% of the time. They differ in annual emissions by 10×. Modeling each source with a duty cycle $\pi$ and a rate-when-on $q$ makes both identifiable: continuous monitors constrain $\pi$ because they sample every hour; aircraft and satellites constrain $q$ because they quantify plumes. This is the formal basis for sensor fusion in the tool.

### 7.5 Why fork LDAR-Sim rather than start from scratch

LDAR-Sim is open source (MIT), maintained, already models a virtual field with stochastic sources and multi-technology detection using METEC POD curves, and has a published independent validation [REF: fox2021; ldarsim-validation-2026]. The validation found that model accuracy was dominated by how repairs were modeled. This version of MRVSim does not model repair, so that weakness does not apply, and the detection and scheduling engine is what gets reused. FEAST is used as an independent cross-check on a subset of runs.

### 7.6 Why a stratified sample rather than every US facility

The US has on the order of a million active wells. Simulating each one at hourly resolution across hundreds of Monte Carlo replications is unnecessary: facilities within a stratum (basin × facility type × throughput class) share emission statistics. The tool simulates a representative sample per stratum and scales by stratum weight. The design doc specifies the strata and a stability test (results must not change materially when strata are refined).

### 7.7 Why calibration is the primary success metric

An interval is a promise: "the truth is in here 90% of the time." The only way to check a promise like that is on cases where the truth is known, which is exactly what the synthetic population provides. A tool that produces narrow intervals that miss the truth 40% of the time is worse than one that produces wide intervals that keep the promise, because the first one certifies facilities that fail. Calibration is checked first, precision second.

### 7.8 Why the tool ships with a Claude gap-analysis panel

The gap between a facility's interval and the bar has at least six causes (quantification error, temporal sampling, detection censoring, spatial completeness, false calls, denominator error). Reading a variance budget and deciding which sensor to add is a multi-variable judgment. A chat panel that has the run's numbers in context lowers the barrier for non-specialist users and shortens the explore-adjust loop.

---

## 8. Glossary for non-domain readers

**Calibration.** A method is calibrated if its stated probabilities match reality: across many cases, a "90% interval" contains the true value about 90% of the time. Measured in this tool by comparing intervals to synthetic truth.

**Credible interval.** The Bayesian version of a confidence interval. A 90% credible interval is the range that contains the true value with 90% probability, given the data and the prior. "Uncertainty interval" and "error bar" mean the same thing in this document.

**Detection limit / detection threshold.** The emission rate at which a sensor detects a source with a stated probability, usually 90% (written POD90). It is a point on the POD curve, not a hard cutoff.

**Duty cycle ($\pi$).** The fraction of time a source is emitting. A source with $\pi = 0.05$ emits about 18 days a year.

**Heavy-tailed distribution.** A distribution where rare large values dominate the total. In methane, a few percent of sources typically emit half or more of the mass.

**Intermittency.** The property of turning on and off. Intermittent sources are the main reason single measurements cannot be annualized.

**Marketed gas.** Gas that is sold, as opposed to gas that is flared, vented, reinjected, or used on site.

**Measurement-informed inventory (MII).** An emissions estimate built from actual measurements rather than from equipment counts multiplied by default emission factors.

**Monte Carlo.** Running a simulation many times with different random draws and looking at the spread of outcomes.

**Observing system completeness ($C$).** The share of emitted methane (from sources above 10 kg/h) that a sensor system could detect at least once. Defined by Jacob et al. 2022.

**OSSE (Observing System Simulation Experiment).** A simulation in which the truth is known, sensors are simulated, and the recovered estimate is compared to the truth. Standard in atmospheric science for designing satellite missions.

**Pareto frontier.** The set of configurations where no metric can be improved without worsening another. Here: cost vs. precision.

**POD curve (probability of detection).** A function giving the probability a sensor detects a source, as a function of the source's rate and the conditions. Measured in blind controlled releases.

**Posterior.** The probability distribution over an unknown quantity after accounting for the observations. The estimator's output.

**Prior.** The probability distribution over an unknown quantity before seeing this facility's observations. In this tool, priors come from published measurement campaigns.

**Quantification error.** The difference between the rate a sensor reports and the true rate. Typically −60% to +90% at 95% confidence for a single aircraft or satellite measurement [REF: daniels2023; sherwin2023].

**Single-blind controlled release.** A test where a known amount of methane is released and the sensor team does not know the rate. The gold standard for measuring POD and quantification error.

**Snapshot.** A single instantaneous measurement of a source. Aircraft and satellite observations are snapshots.

**Stratified sample.** A sample built by dividing the population into groups (strata) and sampling within each, then weighting by group size.

**Super-emitter.** A source emitting above some threshold, commonly 100 kg/h in US regulation.

**Tip-and-cue.** A monitoring strategy where a cheap, broad sensor (a satellite) triggers a targeted, expensive one (an aircraft) when it sees something.

**Variance budget.** A breakdown of the interval width into its contributing error sources.

---

## 9. Attribution policy

Every dataset, POD curve, error distribution, prior, method, and code dependency used by the tool must:

1. Have an entry in `REFERENCES.md` with a stable citation key, authors, year, venue, and DOI or URL.
2. Be referenced by that key in the code (docstring or config) where it is used.
3. Appear in the in-tool Attribution panel whenever it contributes to the displayed result.

DOIs marked `verify` in `REFERENCES.md` must be checked against the publisher before any public release.

---

## 10. Open questions

| # | Question | Owner | Blocking? |
|---|---|---|---|
| Q1 | Can Sherwin et al. 2024 site-level data be extracted at the resolution needed for stratum priors, or only basin aggregates? | Analyst | Yes, for F1 |
| Q2 | Are public well coordinates (state databases, HIFLD, OGIM) complete enough, or is Enverus needed? | Analyst | No (stratified sample does not need every well) |
| Q3 | Which continuous-monitor products have Tier A quantification data, if any? | Analyst | No (they contribute to duty cycle regardless) |
| Q4 | Does LDAR-Sim v4's satellite module implement orbit-based overpass timing or a fixed cadence? | Claude Code | **Resolved 2026-09-30:** neither. v4 has no satellite scheduler (the `orbital` deployment type is an unused constant; only `mobile` and `stationary` are implemented). The legacy V3 branch propagated TLEs with `orbit_predictor` at daily resolution. MRVSim computes overpasses with Skyfield per TDD §5.1. See DECISION_LOG 2026-09-30 "Q4 resolved". |
| Q5 | What is the throughput data source for non-GHGRP facilities? | Analyst | Partially (affects denominator prior) |
| Q6 | How much of LDAR-Sim v4 should the engine actually reuse? v4 is a daily-timestep, agent-based, per-site simulator with no satellite module and a stubbed wind-dependent METEC POD sensor, while TDD §3–5 and §10 specify hourly, vectorised, per-facility arrays. Candidate answer: vendor it as reference/cross-check and reuse its ERA5 tooling, daylight calculator, and parameter conventions; implement the MRVSim hot path natively. | Nidhi | **Provisional answer (Phase 3, 2026-09-30):** MRVSim's observation simulator is native vectorised numpy; LDAR-Sim stays vendored for cross-checks and tooling. Confirm or override; see DECISION_LOG "Phase 3 observation-simulator conventions". |
| Q7 | What ground block does an aircraft site survey actually image around the target (incidental neighbours)? MRVSim uses 0.5 km for Bridger GML as an analyst assumption (`footprint.survey_block_km`, [survey-footprint-assumption]); Kairos and AVIRIS-NG/GAO have none. Needs operator flight plans or published survey geometry. | Nidhi / Analyst | No (affects incidental capture only; added 2026-10-01, DECISION_LOG "Incidental capture") |
| Q8 | What is Tanager-1's maximum off-nadir look angle and along-track scene length? The 29° in `configs/sensors/tanager1.yaml` is derived from the published swath growth (18.6 → 24.2 km, [tanager-spec]), not a stated angle; the scene is assumed square. PRISMA (±14.7°) and EnMAP (±30°) rest on mission pages marked verify. | Analyst | No (Tier B/C sensors; added 2026-10-01) |

---

## 11. Change log

| Date | Change | Author |
|---|---|---|
| 2026-09-30 | Initial draft | Nidhi / Claude |
| 2026-09-30 | §10: Q4 resolved (LDAR-Sim v4 has no satellite scheduler); Q6 added (scope of LDAR-Sim reuse) | Claude (Phase 0) |
| 2026-09-30 | §10: Q6 provisional answer (native simulator) | Claude (Phase 3) |
| 2026-09-30 | §5.4: interval defined as two-sided 90 % [p5, p95]; original w formula marked superseded | Claude (Phase 4) |
| 2026-10-01 | §10: Q7 (aircraft survey footprint) and Q8 (Tanager pointing) added; sensor policy gains `scheduling`/`campaign_days` and `incidental_capture` (TDD §5.1, §8.1; no requirement change) | Claude |
| 2026-10-01 | §5.3: certification bound is the 95 % upper bound, fails at the 5 % lower bound, $P(K \le B)$ reported; §5.4: width is a precision attribute, no longer a certification condition; §5.4a added (evidence ratio, prior-only flag) | Nidhi / Claude |
