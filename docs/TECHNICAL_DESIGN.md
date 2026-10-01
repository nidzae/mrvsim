# Technical Design: Methane MRV Coverage Simulator (MRVSim)

| Field | Value |
|---|---|
| Status | Draft v0.1 |
| Last updated | 2026-09-30 |
| Audience | Reviewers with a background in methane measurement science; the implementing engineer |
| Companion | `PRD.md` (what and why), `REFERENCES.md` (citation keys in square brackets) |

This document states exactly what is computed. Every modeling choice is stated with its justification and its known weakness, so that it can be critiqued. Notation is fixed in §2 and used throughout.

---

## 1. Architecture

```
config (YAML)  ─┐
                ├─►  population  ─►  observation  ─►  estimator  ─►  scoring  ─►  outputs
priors (fitted) ┘     generator       simulator                        & viz
       ▲                                  ▲
       │                                  │
  calibration data              sensor library + policy engine
  (published campaigns)         (POD curves, costs, schedules, rules)
```

Five stages, each a Python module in package `mrvsim`:

| Stage | Module | Input | Output |
|---|---|---|---|
| Population generator | `mrvsim.population` | stratum priors, seed | Synthetic facilities with true sources |
| Observation simulator | `mrvsim.observe` | population, sensor library, policy, conditions | Observation log per facility |
| Estimator | `mrvsim.estimate` | observation log, priors | Posterior samples of KPI-1, KPI-2 per facility |
| Scoring | `mrvsim.score` | posteriors, truth, costs | Calibration, width, certifiability, completeness, cost |
| Optimizer | `mrvsim.optimize` | scoring function, policy parameter space | Pareto frontier |

The simulation engine (population + observation) is a fork of LDAR-Sim v4 [fox2021], with repair disabled and three modules added: observing conditions, the estimator, and scoring. FEAST 3.1 [kemp2016] is used as an independent cross-check in validation test V2.

---

## 2. Notation

| Symbol | Meaning | Units |
|---|---|---|
| $i$ | Facility index | — |
| $j$ | Source index within a facility | — |
| $s$ | Sensor class index | — |
| $t$ | Hour index, $t = 1, \dots, T$, $T = 8760$ | h |
| $K_i$ | Number of sources at facility $i$ | — |
| $z_{ij}$ | Source type: 0 = steady, 1 = intermittent | — |
| $q_{ij}$ | Emission rate of source $j$ when on | kg/h |
| $\pi_{ij}$ | Duty cycle of source $j$ | — |
| $S_{ij}(t)$ | On/off state at hour $t$, $\in \{0, 1\}$ | — |
| $Q_i(t)$ | Facility total rate at hour $t$: $\sum_j S_{ij}(t) \, q_{ij}$ | kg/h |
| $M_i$ | True annual emitted mass: $\sum_t Q_i(t)$ | kg/yr |
| $I_i$ | True methane intensity (PRD §5.1) | — |
| $G_i$ | Marketed methane mass over the year | kg/yr |
| $P_s(q, c)$ | Probability sensor $s$ detects a source of rate $q$ under conditions $c$ | — |
| $r$ | Reported rate from a detection | kg/h |
| $u$ | Wind speed at 10 m | m/s |
| $\mathcal{D}_i$ | Observation log for facility $i$ | — |

---

## 3. Population generator

### 3.1 Stratification

Facilities are grouped into strata defined by:

- **Basin** (Permian, Appalachian, Haynesville, Eagle Ford, Bakken, DJ, Anadarko, San Juan, Uinta, other), chosen to match the regions in [sherwin2024] and [cusworth2022].
- **Facility type** (well pad: oil-dominant / gas-dominant / mixed; gathering compressor station; processing plant; transmission compressor station; storage).
- **Throughput class** (terciles within basin × type, from EPA GHGRP Subpart W and state production data [ghgrp; state-production-data]).

Each stratum $h$ has a weight $W_h$ equal to its share of national facility count (for KPI-2 aggregates) and of national throughput (for KPI-1 aggregates). The tool simulates $n_h$ facilities per stratum (default $n_h = 100$, minimum 30) and scales outputs by $W_h / n_h$.

**Implementation note (Phase 1):** only basin × type cells that exist in the US population are enumerated (`configs/strata.yaml`, 21 cells → 63 strata at terciles). Throughput classes are quantile bands of the cell's throughput distribution by construction, so the tercile → quintile switch for V4 needs no refit. Weights are PLACEHOLDER until fitted from [ghgrp; state-production-data]; see DECISION_LOG 2026-09-30 "Default strata list".

**Stability test (V4):** results must change by less than the Monte Carlo standard error when throughput terciles are replaced by quintiles.

### 3.2 Source count

$$
K_i \sim \text{Poisson}(\lambda_h) + 1
$$

with $\lambda_h$ fitted per stratum so that the simulated distribution of facility-level totals matches the site-level distribution in [sherwin2024]. The +1 guarantees at least one source, since component-level surveys find essentially no fully leak-free sites [rutherford2021].

**Weakness:** Poisson under-disperses relative to real source counts. Negative binomial is a drop-in replacement once site-level data support fitting a dispersion parameter (open question Q1 in the PRD).

### 3.3 Source type and rate

$$
z_{ij} \sim \text{Bernoulli}(p_{h}), \qquad
\ln q_{ij} \sim \begin{cases}
\mathcal{N}(\mu_{h,0}, \sigma_{h,0}^2) & z_{ij} = 0 \\
\mathcal{N}(\mu_{h,1}, \sigma_{h,1}^2) & z_{ij} = 1
\end{cases}
$$

with a Pareto tail spliced above a stratum-specific rate $q_{\text{tail},h}$:

$$
P(q > x \mid x > q_{\text{tail},h}) = \left(\frac{x}{q_{\text{tail},h}}\right)^{-\alpha_h}
$$

Parameters are fitted so that (a) the rate distribution of detected sources above 10 kg/h matches [cusworth2022] and [sherwin2024] per basin, and (b) the sub-10 kg/h mass matches the component-level model of [rutherford2021].

**Implementation note (2026-10-01, DECISION_LOG "Fitted emission priors"):** the steady lognormal is moment-matched to Rutherford's component database; the remaining parameters are fitted per basin to the site-level survival curves of the Sherwin 2024 correction release and nationally to the Omara site distribution and the Cusworth point-source share. A single $p_h$ cannot reproduce both a high low-level emitting fraction and low persistence of large sources; size-dependent intermittency is a version-2 item. Intermittent sources are given a higher $\mu_{h,1}$ than steady ones, consistent with the finding that short- and long-duration sources contribute comparably to point-source totals [cusworth2022].

**Weakness:** the splice point and $\alpha_h$ are poorly constrained by data below the aircraft detection limit. The prior misspecification test (V5) perturbs $\alpha_h$ by ±0.3 and checks that calibration degrades gracefully.

### 3.4 Temporal process

Each intermittent source follows an **alternating renewal process**: it stays on for a random duration $D_{\text{on}}$, then off for $D_{\text{off}}$, and repeats. Durations are drawn independently:

$$
\ln D_{\text{on}} \sim \mathcal{N}(\nu_{\text{on},h}, \tau_{\text{on},h}^2), \qquad
\ln D_{\text{off}} \sim \mathcal{N}(\nu_{\text{off},h}, \tau_{\text{off},h}^2)
$$

The duty cycle is the ratio of mean durations:

$$
\pi_{ij} = \frac{\mathbb{E}[D_{\text{on}}]}{\mathbb{E}[D_{\text{on}}] + \mathbb{E}[D_{\text{off}}]}
$$

Duration parameters are fitted to (a) continuous-monitor event-duration distributions [daniels2023; cms-duration-2024] and (b) repeat-overflight persistence statistics: the mean intermittency of 0.23 in California [duren2019] and the multi-year revisit persistence in [cusworth2022]. Steady sources have $\pi = 1$.

The state path $S_{ij}(t)$ is generated for every hour and stored, so that the observation simulator can read the true state at any observation time.

**Implementation note (Phase 1, DECISION_LOG 2026-09-30 "Phase 1 implementation choices"):** each intermittent source starts in the stationary state of its renewal process: on with probability $\pi_{ij}$, with the residual of the current interval drawn as $U \cdot D^*$ where $D^*$ follows the length-biased lognormal $\mathcal{N}(\nu + \tau^2, \tau^2)$ in log space. $S_{ij}(t)$ is the state at the midpoint of hour $t$.

**Weaknesses:**
- Lognormal durations are lighter-tailed than some observed event distributions. If rare multi-week events are under-represented, $\pi$ is biased low. V1 checks the simulated persistence against [cusworth2022].
- No diurnal or seasonal structure in version 1. This matters most for sun-synchronous satellites, which always observe at the same local time. Flagged for version 2 (`DECISION_LOG.md`).

### 3.5 Throughput and denominator

$$
G_i = V_{\text{gas,mkt},i} \cdot \rho_{\text{CH}_4} \cdot X_{\text{CH}_4,i}, \qquad
\ln X_{\text{CH}_4,i} \sim \mathcal{N}(\ln 0.88, 0.05^2) \text{ (basin-adjusted)}
$$

$V_{\text{gas,mkt},i}$ is drawn from the stratum's throughput distribution. The true $G_i$ is known to the population generator; the estimator sees only a noisy version (§6.6).

### 3.6 Observing conditions per facility

For each facility and month $m$:

- $p_{\text{cloud},i,m}$: daytime cloud fraction from MODIS climatology [modis-cloud].
- $\rho_{\text{surf},i}$: mean SWIR surface reflectance and heterogeneity index from Landsat/Sentinel-2 composites [landsat-composite].
- $u_{i,m}$: wind speed distribution (Weibull) from ERA5 or HRRR [era5].
- Solar zenith angle at each satellite overpass, computed from date, time, and coordinate.

---

## 4. Sensor library

Each sensor class $s$ is a record with the following fields, each carrying a citation key.

| Field | Symbol | Description |
|---|---|---|
| POD curve | $P_s(q, c)$ | §4.1 |
| Quantification error | $\beta_s, \sigma_s$ | §4.2 |
| False-positive rate | $\lambda_{\text{FP},s}$ | Per observation opportunity |
| Operating constraints | — | Max wind, min solar zenith, max cloud, day-only |
| Observation mode | — | snapshot / continuous / survey |
| Spatial scope | — | site-level total / per-source |
| Revisit or schedule | — | orbit (satellite), campaign (aircraft), survey list (drone/OGI), hourly (CMS) |
| Field of view and pointing (added 2026-10-01) | — | Tasked imagers: scene extent along/cross-track and maximum off-nadir pointing angle (`orbit.fov_along_km`, `fov_cross_km`, `max_off_nadir_deg`); aircraft: surveyed block around a site (`footprint.survey_block_km`). Drive the field of regard and incidental capture of §5.1 |
| Cost | — | Per site-visit, per tasking, or per site-year |
| Validation tier | A–D | PRD §6.2 N2 |

### 4.1 POD curve

A logistic function of log effective rate:

$$
P_s(q, c) = \frac{1}{1 + \exp\!\left(-\left[a_s + b_s \ln q_{\text{eff}}\right]\right)}, \qquad
q_{\text{eff}} = q \cdot \left(\frac{u_{\text{ref}}}{u}\right)^{\gamma_s} \cdot \phi_s(\rho_{\text{surf}})
$$

- $a_s, b_s$ are fitted by maximum likelihood to the detection/non-detection table of the sensor's blind controlled-release study.
- $\gamma_s$ captures wind dependence: at higher wind the plume is more dilute and harder to see. Default $\gamma_s = 1$ for imaging spectrometers (enhancement scales inversely with wind), $\gamma_s = 0$ for continuous monitors, with study-specific overrides.

  **Implementation note (Phase 3, DECISION_LOG 2026-09-30):** the wind term is floored, $u \to \max(u, u_{\min})$ with $u_{\min} = 2$ m/s, because retrievals do not keep improving toward zero wind.
- $\phi_s(\rho_{\text{surf}})$ is a surface adjustment factor, equal to 1 at the reflectance of the test site and declining for darker or more heterogeneous surfaces. **This factor is the weakest-constrained element of the model.** Version 1 uses a linear decline to 0.5 at the 10th percentile of US oil-and-gas-region reflectance, with a uniform ±50% uncertainty propagated into the estimator's likelihood. Flagged in the attribution panel as an assumption, not a measured curve.

Sources of POD data by sensor class:

| Sensor class | Blind-test source | Tier |
|---|---|---|
| Aircraft: Bridger GML | [sherwin2021; elabbadi2024; chen2024] | A |
| Aircraft: Kairos | [sherwin2021; elabbadi2024] | A |
| Aircraft: AVIRIS-NG / GAO | [elabbadi2024; ayasse2024] | A |
| Satellite: GHGSat, Sentinel-2, Landsat, PRISMA, EnMAP | [sherwin2023] | A |
| Satellite: Tanager-1 | Designed limit 0.05–0.15 t/h [sherwin2023 (citing design); carbon-mapper] — controlled-release results to be added when published | B until then |
| Satellite: TROPOMI | Detection statistics from [schuit2023; lauvaux2022] — no blind test possible at its scale | B |
| Continuous monitors (various) | METEC ADED protocol [metec-aded; bell2023; ilonze2024] | A for detection; C for quantification unless product-specific |
| Drone / OGI | [ravikumar2018; zimmerle2020] | A |

**Note on sample size:** satellite POD curves rest on far fewer blind releases than aircraft curves; rigorous characterization needs on the order of 100 releases, more than 10× what any satellite had in [sherwin2023]. The estimator therefore treats $(a_s, b_s)$ for satellites as uncertain (posterior from the fit, not point values) and propagates that uncertainty.

### 4.2 Quantification error

For a detected source of true rate $q$, the reported rate is

$$
\ln r = \ln q + \beta_s + \epsilon, \qquad \epsilon \sim \mathcal{N}(0, \sigma_s^2)
$$

$\beta_s$ is the sensor's multiplicative bias and $\sigma_s$ its log-scale spread, both fitted to the blind-test regression of reported vs. released rate. Representative values: for aircraft and satellites, $\sigma_s \approx 0.35$–$0.45$, corresponding to roughly −60% to +90% at 95% [daniels2023; sherwin2023]. Continuous-monitor quantification uses product-specific METEC results or, absent those, $\sigma_s = 0.9$ with the source flagged Tier C.

**Correlated error (version 2):** wind error on one aircraft campaign day is shared by all sites flown that day. Version 1 treats $\epsilon$ as independent, which understates interval width for basin aggregates. Version 2 adds a per-campaign term $\epsilon_{\text{camp}} \sim \mathcal{N}(0, \sigma_{\text{camp}}^2)$ shared across sites.

### 4.3 Aggregation across sources

Aircraft and satellite snapshots resolve a facility, not individual sources, when sources are within one pixel or plume-overlap distance. The observation simulator therefore reports the facility total $Q_i(t)$ (with quantification error applied to the total) for snapshot sensors. Ground surveys (drone, OGI) resolve individual sources and report per-source detections.

### 4.4 False positives

Each observation opportunity yields a false detection with probability $\lambda_{\text{FP},s}$ (from the blind-test false-alarm table), with a reported rate drawn from the lower part of the sensor's detected-rate distribution.

---

## 5. Observation simulator

For each facility, sensor, and hour, the simulator decides whether an observation opportunity exists, whether it is usable, and what it reports.

### 5.1 Observation opportunities

- **Satellites:** overpass times are computed by propagating public two-line orbital elements with Skyfield [skyfield] and testing whether the facility lies within the instrument swath. For tasked instruments (GHGSat, Tanager), an overpass is used only if the policy assigns tasking to that facility (§8). TROPOMI observes every daylight overpass.
  *(Amended 2026-10-01, DECISION_LOG "Incidental capture": for a **tasked** imager the swath test is replaced by a **field-of-regard** test. With altitude $h$ from the pinned TLE's mean motion and maximum off-nadir angle $\theta$, the ground reach from the sub-satellite point is $R\,[\arcsin((R+h)/R \cdot \sin\theta) - \theta]$, and a facility can be tasked on any pass whose ground track lies within swath/2 + reach of it (GHGSat-C: ±15° from ~450–500 km, a band of roughly 260 km instead of 12 km). When a facility is tasked, the instrument frames a **scene** of `fov_along_km` × `fov_cross_km` centred on it and aligned with the ground-track heading at closest approach; every other facility inside that rectangle receives an **incidental** snapshot at the same hour, with the target's cloud state (same sky), its own sun angle and wind, and a detection draw from the same POD. Incidental rows are logged with `incidental = true` and the target's index, are not charged as taskings, and enter the likelihood, the evidence counts and completeness like any other snapshot. A (facility, hour) pair is logged once even if it lies in two overlapping scenes or is itself tasked. Off-nadir elongation of the footprint (≤ 4 % at 15°) and the ±15° along-track pointing (which shifts timing, not which passes qualify) are ignored. Aircraft site surveys do the same with an axis-aligned block of `footprint.survey_block_km` around the visited site. The policy flag `incidental_capture` (default true) switches the mechanism off for comparison.)*
- **Aircraft:** the policy assigns each facility a list of campaign dates. *(Amended 2026-10-01, DECISION_LOG "Regional flight campaigns": with `scheduling: campaign`, each basin is flown in one window of `campaign_days` consecutive days per slice of the year and every covered facility in the basin gets one visit inside that window; with `scheduling: independent`, the default for existing configs, each facility's dates are drawn on their own as before.)*
- **Drone / OGI:** the policy assigns survey dates.
- **Continuous monitors (CMS):** every hour at instrumented facilities.

### 5.2 Usability gates

An opportunity is usable if all of the following hold, with draws from the facility's monthly condition distributions:

| Gate | Satellites | Aircraft | Drone/OGI | CMS |
|---|---|---|---|---|
| Cloud | $\text{Bernoulli}(1 - p_{\text{cloud},i,m})$ | Same, with a looser threshold | — | — |
| Solar zenith | $\le 70°$ | — | — | — |
| Wind | $u \le u_{\max,s}$ | $u \le u_{\max,s}$ | $u \le u_{\max,s}$ | Wind sector must intersect sensor placement |
| Outage | — | — | — | $\text{Bernoulli}(1 - p_{\text{outage}})$ |

**Implementation note (Phase 3, DECISION_LOG 2026-09-30):** the aircraft cloud gate blocks with probability $p_{\text{cloud}} (1 - c_s)$ where $c_s$ is the sensor's cloud tolerance (`max_cloud_fraction` in its YAML); CMS outage and wind-sector gates are independent per hour; tasked satellites use one overpass per equal slice of the year up to the tasking frequency.

### 5.3 Detection and reporting

At a usable opportunity at hour $t$:

1. Read the true state $S_{ij}(t)$ and compute the observable rate ($Q_i(t)$ for snapshot sensors; $S_{ij}(t) q_{ij}$ per source for surveys and CMS).
2. Draw detection: $\text{Bernoulli}(P_s(q_{\text{obs}}, c_t))$. If $q_{\text{obs}} = 0$, apply the false-positive rule instead.
3. If detected, draw the reported rate from §4.2.
4. Append $(t, s, \text{detected}, r, c_t)$ to $\mathcal{D}_i$. Non-detections are recorded, with their conditions; they are informative.

For CMS the log is an hourly detection/non-detection time series plus (discounted) rate estimates.

---

## 6. Estimator

The estimator sees only $\mathcal{D}_i$, the sensor library (POD parameters and error distributions), the stratum priors, and a noisy throughput. It never sees $S_{ij}(t)$, $q_{ij}$, or $K_i$.

### 6.1 Latent variables

For facility $i$: $K_i$, and for each source $j \le K_i$: $z_{ij}, q_{ij}, \pi_{ij}$. Since $K_i$ is unknown, the model uses data augmentation with a fixed upper bound $K_{\max}$ (default 8) and an inclusion indicator $\omega_{ij} \in \{0,1\}$ for each candidate source; $K_i = \sum_j \omega_{ij}$.

### 6.2 Priors

Priors are the stratum distributions from §3.2–3.4, i.e., the same parametric families with the fitted hyperparameters. This is empirical Bayes: the prior is fitted to published campaign data, not to the facility being estimated.

**Critique to anticipate:** in an OSSE, truth is generated from the same distribution as the prior, which makes calibration easier than in reality. The prior misspecification test (V5) addresses this by generating truth from perturbed distributions ($\alpha_h \pm 0.3$, $\mu_h \pm 0.3$, $\nu_{\text{on}} \pm 0.5$) while the estimator keeps the unperturbed prior, and reporting calibration under each perturbation.

### 6.3 Likelihood: snapshot sensors

At snapshot observation $k$ (aircraft or satellite) at hour $t_k$ with conditions $c_k$, the facility's observable rate is $Q_i(t_k) = \sum_j \omega_{ij} S_{ij}(t_k) q_{ij}$. Because snapshots are sparse relative to event durations, the state at each snapshot is treated as an independent draw $S_{ij}(t_k) \sim \text{Bernoulli}(\pi_{ij})$ (marginalizing the renewal process). The likelihood of a **non-detection** is

$$
\mathcal{L}_k^{\text{ND}} = \sum_{\mathbf{S} \in \{0,1\}^{K}} \left[\prod_j \pi_{ij}^{S_j} (1 - \pi_{ij})^{1 - S_j}\right] \left(1 - P_s\!\left(\sum_j S_j q_{ij}, c_k\right)\right)
$$

which is the "off or missed" term of [ayasse2024] extended to multiple sources. For $K_{\max} = 8$ the sum has 256 terms and is computed exactly.

The likelihood of a **detection with reported rate** $r_k$ is

$$
\mathcal{L}_k^{\text{D}} = \sum_{\mathbf{S}} \left[\prod_j \pi_{ij}^{S_j} (1 - \pi_{ij})^{1 - S_j}\right] P_s(Q_{\mathbf{S}}, c_k) \cdot \mathcal{N}\!\left(\ln r_k \mid \ln Q_{\mathbf{S}} + \beta_s, \sigma_s^2\right)
$$

with $Q_{\mathbf{S}} = \sum_j S_j q_{ij}$, plus a false-positive mixture component with weight $\lambda_{\text{FP},s}$.

**Validity condition:** the independent-draw assumption holds when the gap between snapshots exceeds typical event durations. For two snapshots within one day, the states are correlated; version 1 accepts the small error, version 2 uses the renewal-process transition probability.

### 6.4 Likelihood: continuous monitors

CMS data are an hourly binary series $d_t$ per facility (detected / not). For an alternating renewal process observed hourly with detection probability $P_{\text{CMS}}(q, c_t)$, the exact likelihood is a hidden Markov model with the on/off state hidden and $d_t$ emitted. Version 1 uses the **run-length summary**: the empirical distribution of detected-run lengths and non-detected-run lengths, which directly constrains $(\nu_{\text{on}}, \tau_{\text{on}}, \nu_{\text{off}}, \tau_{\text{off}})$ and therefore $\pi$, with a correction for missed hours using $P_{\text{CMS}}$. Version 2 implements the full HMM forward algorithm.

CMS rate estimates enter with $\sigma_s = 0.9$ (Tier C) unless a product-specific METEC quantification result exists.

### 6.5 Likelihood: ground surveys

Drone and OGI surveys resolve sources. A survey at hour $t$ reports, per candidate source, detected or not, with per-source POD. Detections update $\omega_{ij}$ (source exists), $q_{ij}$ (via the survey's quantification error), and $S_{ij}(t)$.

### 6.6 Denominator

The estimator receives $\hat{G}_i = G_i \cdot \exp(\eta)$ with $\eta \sim \mathcal{N}(0, \sigma_G^2)$, $\sigma_G = 0.05$ for GHGRP-reporting facilities and $0.15$ otherwise, and treats $G_i$ as a latent with that likelihood.

### 6.7 Posterior and KPI derivation

The joint posterior is

$$
p(\boldsymbol{\theta}_i \mid \mathcal{D}_i) \propto p(\boldsymbol{\theta}_i) \prod_k \mathcal{L}_k
$$

with $\boldsymbol{\theta}_i = \{\omega_{ij}, z_{ij}, q_{ij}, \pi_{ij}\}_{j \le K_{\max}} \cup \{G_i\}$. Posterior samples are drawn with NUTS in PyMC [pymc] (exact method; used in validation runs and for any facility the user drills into) or with sequential importance sampling from the prior with $10^4$ draws (fast approximation; used for interactive recompute). The tool reports which method produced the displayed interval.

Each posterior sample gives

$$
\hat{M}_i = \sum_j \omega_{ij} \, \pi_{ij} \, q_{ij} \, T, \qquad
\hat{I}_i = \frac{f_{\text{gas},i} \, \hat{M}_i}{G_i}
$$

The 10th, 50th, and 90th percentiles of $\hat{M}_i$ and $\hat{I}_i$ across samples are the reported lower bound, median, and upper bound. *(Superseded 2026-09-30: the reported two-sided 90 % interval is the 5th and 95th percentiles; the 90th percentile remains the one-sided certification bound $\hat{K}_{U,90}$. See DECISION_LOG "two-sided 90 % credible interval".)*

*(Amended 2026-10-01, DECISION_LOG "certification is the compliance decision at 95 %": the certification bound is the 95th percentile and the failure bound the 5th. The estimator persists the full posterior quantile grid (percentiles 0–100 in steps of 1, `posterior_*_quantiles`), so $P(K \le B \mid \text{data})$ is read off at any bar by interpolation; the prior's 5th/50th/95th percentiles on the same draws (`prior_*_pcts`, the posterior with no observations) and the per-facility counts of usable snapshots, survey visits and CMS hours (`evidence_counts`) are persisted for the evidence ratio of PRD §5.4a.)*

**Implementation notes (Phase 4, DECISION_LOG 2026-09-30):**
- *Estimand.* The reported $\hat{M}_i$ is the posterior predictive of the realised annual mass: each intermittent source's on-hours $H_{ij}$ is drawn per posterior sample from a lognormal with mean $\pi_{ij} T$ and renewal-reward variance $T[(1-\pi)^2 \mathrm{Var}\,D_{\text{on}} + \pi^2 \mathrm{Var}\,D_{\text{off}}]/(\mathbb{E}D_{\text{on}} + \mathbb{E}D_{\text{off}})$, and $\hat{M}_i = \sum_j \omega_{ij} q_{ij} H_{ij}$. The expression above is the expectation of this predictive and is available as the `realised=False` option. See the decision log for why this is required for calibration against $M_i = \sum_t Q_i(t)$.
- *Sampler.* Importance sampling from the prior with $10^4$ draws, with an automatic switch to tempered sequential Monte Carlo (resample-move) when the effective sample size is below 200.
- *Snapshot likelihood.* The $2^K$ sum is evaluated exactly, grouped by the drawn $K$; non-detections are grouped by (sensor, 0.5 m/s wind bin); factors whose maximal POD is below $10^{-5}$ are skipped.
- *CMS likelihood (v1 definition of §6.4).* Composite Gaussian likelihood on (i) the detected-hour fraction with predicted value $p = p_{\text{true}} + \lambda_{\text{FP}}(1 - p_{\text{true}})$, $p_{\text{true}} = 1 - \prod_j (1 - \omega_j \pi_j P_j)$, and variance $p(1-p)/n_{\text{runs}}$; (ii) the log mean detected-run length with predicted value from the OR-over-sources hazard $h = \sum_j w_j\,[1/(u\,\mathbb{E}D_{\text{on},j}) + (1 - P_j)]\,\prod_{k \ne j}(1 - d_k)$, $d_j = \omega_j \pi_j P_j$, $w_j = d_j/\sum d$, $u$ the usable fraction, mixed with $\lambda_{\text{FP}} n_u (1-p_{\text{true}})$ false-call singleton runs; (iii) the mean log reported rate, whose expectation is the lognormal-moment-matched $\mathbb{E}[\ln \sum_j \mathrm{det}_j r_j \mid \text{flagged}]$ mixed with the false-call rate law, with $\sigma = 0.9$.
- *Survey likelihood (§6.5).* Poisson-process association: $\log L = \sum_j \log(1 - d_j) + \sum_k \log\big(\sum_j \tfrac{d_j}{1-d_j} \mathcal{N}(\ln r_k \mid \ln q_j + \beta, \sigma^2) + \lambda_{\text{FP}} f_{\text{FP}}(r_k)\big)$ with $d_j = \omega_j \pi_j P_s(q_j)$.

### 6.8 Variance budget

For the drill-down view, the interval width is decomposed by re-running the posterior with one error source at a time set to its true value (an oracle ablation):

| Component | Oracle set |
|---|---|
| Quantification | $\sigma_s = 0$ |
| Temporal sampling | $\pi_{ij}$ fixed at truth |
| Detection censoring | Sources below the deployed POD10 revealed |
| Spatial completeness | Facility observed at every policy-scheduled opportunity regardless of cloud/wind |
| False calls | $\lambda_{\text{FP}} = 0$ |
| Denominator | $\sigma_G = 0$ |

The width reduction from each ablation, normalized to sum to the total width, is the budget. This is a diagnostic, not a formal variance decomposition; components interact.

---

## 7. Scoring

For a configuration, across all simulated facilities and $R$ Monte Carlo replications (default $R = 200$):

| Metric | Definition |
|---|---|
| **Calibration** $\kappa$ | Fraction of (facility, replication) pairs where the true KPI lies within the two-sided 90% interval $[\hat{K}_5, \hat{K}_{95}]$. Target: $0.85 \le \kappa \le 0.95$. Reported separately for KPI-1 and KPI-2 and per stratum |
| **Width** $w$ | Median over facilities of $(\hat{K}_{95} - \hat{K}_{5}) / (2\hat{K}_{50})$ (amended 2026-09-30; originally $(\hat{K}_{U,90} - \hat{K}_{L,10})$, an 80 % interval) |
| **Bias** | Median of $(\hat{K}_{50} - K_{\text{true}}) / K_{\text{true}}$ |
| **Certifiable share** | Fraction of facilities (and of throughput, stratum-weighted) with $\hat{K}_{U,90} \le B$ and width $\le w_{\max}$ *(superseded 2026-10-01)*; **amended:** with $\hat{K}_{95} \le B$ (decision only) |
| **Fails share** | Fraction with $\hat{K}_{5} > B$ (amended 2026-10-01; was $\hat{K}_{L,10} > B$) |
| **Indeterminate share** | Fraction with interval straddling $B$ or width $> w_{\max}$ *(superseded 2026-10-01)*; **amended:** interval straddles $B$ |
| **Precise share** | Fraction with $w \le w_{\max}$; also reported as the certified-and-precise share (facilities and throughput) (added 2026-10-01) |
| **Prior-only share** | Fraction of certified facilities (and of certified throughput) whose evidence ratio $e > $ `prior_only_ratio` or with no usable observation (PRD §5.4a; added 2026-10-01) |
| **Evidence ratio** $e$ | Per facility, $\ln(\hat{K}_{95}/\hat{K}_{5})$ of the posterior over the same for the prior on common random numbers (added 2026-10-01) |
| **Completeness** $C$ | PRD §5.5, with $\bar{P}_j = 1 - \prod_k (1 - P_s(q_j, c_k))$ over the year's usable opportunities |
| **Cost** | Sum of sensor costs for the deployment; cost per tonne detected; cost per certified MMBtu |

Monte Carlo standard errors are reported for every metric.

**Implementation note (Phase 5, DECISION_LOG 2026-09-30 "Scoring conventions"):** fails ⇔ p10 > B *(superseded 2026-10-01: fails ⇔ p5 > B, certified ⇔ p95 ≤ B)*; completeness uses the realised usable opportunities with per-opportunity wind/surface factors and ignores the source state; costs are per visit scheduled / per tasking / per site-year as in each sensor YAML; with R = 1 the calibration SE is binomial over facilities. `mrvsim.pipeline.run_scored` runs the R replications and writes `runs/<id>/summary.json`.

---

## 8. Policy engine and optimizer

### 8.1 Policy parameters

| Parameter | Per sensor class | Range |
|---|---|---|
| Enabled | yes/no | — |
| Coverage fraction | share of facilities, or of throughput, instrumented or surveyed | 0–1 |
| Frequency | surveys per year (aircraft, drone, OGI); tasking priority (satellite) | 0–52 |
| Targeting | random / throughput-weighted / prior-risk-weighted / widest-interval-first *(wording superseded 2026-10-01: `throughput` is a top-k cutoff by marketed gas, "top facilities by throughput")* | categorical |
| Scheduling (campaign/survey sensors) | independent dates / regional campaign with `campaign_days` per basin (added 2026-10-01) | categorical, 1–60 days |
| Validation tier filter | minimum tier | A–D |

### 8.2 Adaptive rules (tip-and-cue)

Rules are evaluated daily inside the simulation:

- `IF satellite_detect(site, rate ≥ X) THEN schedule aircraft(site, within N days)`
- `IF cms_run_length(site) ≥ H hours THEN schedule drone(site, within N days)`
- `IF remaining_aircraft_budget > 0 THEN allocate to argmax posterior_width(site)` (evaluated monthly using the fast estimator)

Each rule has parameters $(X, N, H)$ exposed to the optimizer.

### 8.3 Optimizer

Objective: minimize annual cost subject to $\kappa \ge 0.85$, $w \le w_{\max}$, and certifiable throughput share $\ge \theta$, for user-chosen $(B, w_{\max}, \theta)$.

Method: Bayesian optimization over the continuous parameters with categorical rule switches, using Optuna [optuna], with each trial being a full scoring run at reduced $R$ (default 50) and the top candidates re-scored at full $R$. The Pareto frontier is the set of non-dominated (cost, $w$) pairs across all trials. Reinforcement learning is explicitly not used in version 1; the parameter space is small enough for direct search and the resulting frontier is easier to explain.

**Implementation note (Phase 7, DECISION_LOG 2026-09-30 "Policy engine conventions"):** NSGA-II with constraints κ ≥ κ_min and certified throughput share ≥ θ, objectives (cost, w); rules in §8.2 are evaluated in two passes (triggering sensors first, cued visits merged, full simulation) which is exact while cued sensors do not themselves trigger rules; the widest-interval rule runs the fast estimator monthly.

*(Note 2026-10-01: the certifiable-throughput constraint θ uses the decision-only certified share of §7 as amended; $w \le w_{\max}$ remains the feasibility check on the median width, so the optimizer still searches for precise configurations even though precision no longer gates individual certifications.)*

---

## 9. Validation tests

Each test is a pytest module with a pass/fail threshold. The tool displays results in the Validation panel.

**Implementation note (Phase 6, DECISION_LOG 2026-09-30 "Validation framework conventions"):** the comparison logic lives in `mrvsim.validate.v1_rates … v7_calibration` with pytest wrappers in `tests/validation/`; published values come from `data/fitted/validation_targets.yaml` with citation keys and status; all seven refuse to run against PLACEHOLDER priors. V1 falls back to a quantile-band check when no published sample is available; V2's FEAST cross-check feeds steady sources as constant emissions and intermittent sources through FEAST's episodic model.

| ID | Test | Pass criterion |
|---|---|---|
| **V1** | Simulated detected-rate distribution above 10 kg/h per basin vs. [cusworth2022] and [sherwin2024]; simulated persistence vs. [cusworth2022] | Kolmogorov–Smirnov p > 0.05 per basin; persistence within published CI |
| **V2** | Simulated basin totals vs. [sherwin2024] basin totals; cross-check with FEAST 3.1 using the same population | Within published 95% CI; FEAST and MRVSim within 15% |
| **V3** | Reproduce published measurement-informed basin intensities using the sensor mix those studies used: Haynesville 0.79% [0.63, 0.98] and Permian 4.6% [4.4, 4.9] [haynesville-2025]; reproduce the reported sensitivity to basin boundary definition | Published estimate inside MRVSim interval; boundary-sensitivity factor within ±30% |
| **V4** | Stratum stability: terciles → quintiles | All headline metrics change by less than 2× Monte Carlo SE |
| **V5** | Prior misspecification: truth generated from perturbed $\alpha_h, \mu_h, \nu_{\text{on}}$ | $\kappa \ge 0.80$ under every single perturbation; report $\kappa$ under joint perturbation |
| **V6** | Transient-event low bias: simulate a multi-hour blowdown observed by LEO satellites only, per the VLMR experiment [vlmr-2026] | MRVSim reproduces satellite underestimation of total released mass, direction and order of magnitude |
| **V7** | Calibration on default configuration | $0.85 \le \kappa \le 0.95$ for both KPIs |

---

## 10. Computational budget

- Population: $n_h \times |h| \approx 100 \times 60 = 6{,}000$ facilities, $\le 8$ sources each, $8{,}760$ hours → $4 \times 10^8$ state values per replication, stored as bit-packed arrays (≈ 50 MB).
- Observation: vectorized over facilities per hour; ≈ 10 s per replication in NumPy.
- Estimator, fast path: $10^4$ importance-sampling draws × 6,000 facilities ≈ 20 s.
  **Measured (Phase 4, 2026-09-30):** 50–110 ms per facility in NumPy with the grouped exact enumeration and SMC fallback, i.e. 340–700 s for 6,300 facilities at $10^4$ draws; ~15 ms per facility at 2,000 draws. The 20 s figure requires compiled kernels and/or fewer draws; see DECISION_LOG "Fast path" and the Phase 4 status note.
- Estimator, exact path (NUTS): ≈ 5–20 s per facility; run only on validation subsets and on drill-down.
- Scoring with $R = 200$: ≈ 1–2 hours batch on a laptop for the exact path; interactive recompute uses $R = 20$ and the fast path (< 60 s, meeting PRD N3).

---

## 11. Known limitations (version 1)

1. **Surface adjustment factor** $\phi_s$ is assumed, not measured. Largest single unvalidated element.
2. **Independent quantification errors** across sites on the same campaign day understate aggregate interval width.
3. **Lognormal durations** may under-represent rare long events.
4. **No diurnal/seasonal emission structure**, which biases satellite-only configurations in an unknown direction.
5. **Snapshot state independence** is violated for observations closer together than event durations.
6. **CMS likelihood** uses run-length summaries rather than the full HMM.
7. **Source count** is capped at $K_{\max} = 8$; facilities with more distinct sources have their smaller sources merged.
8. **Satellite POD parameters** rest on small blind-test samples; their posterior uncertainty is propagated but is itself uncertain.
9. **Priors and truth share a parametric family**, so calibration in the OSSE is an upper bound on calibration in the field; V5 bounds the degradation.
10. **Realisation variance is approximated** by a lognormal with renewal-reward CLT variance (Phase 4); for CMS facilities part of it is double-counted (conservative) because the observed detected fraction already reflects the realisation.

Each limitation maps to a version-2 item in `DECISION_LOG.md`.

---

## 12. Data and code dependencies

| Item | Role | Key |
|---|---|---|
| LDAR-Sim v4 | Simulation engine (forked) | [fox2021; ldarsim-repo] |
| FEAST 3.1 | Cross-check | [kemp2016; feast-repo] |
| Sherwin et al. 2024 | Rate distributions, basin totals | [sherwin2024] |
| Cusworth et al. 2022 | Point-source shares, persistence | [cusworth2022] |
| Rutherford et al. 2021 | Sub-threshold mass | [rutherford2021] |
| Duren et al. 2019 | Intermittency baseline | [duren2019] |
| Daniels et al. 2023 | Event durations, quantification error | [daniels2023] |
| Ayasse et al. 2024 | Non-detection likelihood | [ayasse2024] |
| Jacob et al. 2022 | Completeness metric | [jacob2022] |
| Sherwin et al. 2023 | Satellite POD and error | [sherwin2023] |
| El Abbadi et al. 2024; Sherwin et al. 2021 | Aircraft POD and error | [elabbadi2024; sherwin2021] |
| METEC ADED; Bell 2023; Ilonze 2024 | CMS POD | [metec-aded; bell2023; ilonze2024] |
| Haynesville/Permian intensity study | V3 | [haynesville-2025] |
| VLMR experiment | V6 | [vlmr-2026] |
| EPA GHGRP Subpart W; state production data | Throughput | [ghgrp; state-production-data] |
| MODIS cloud, ERA5/HRRR wind, Landsat composites | Conditions | [modis-cloud; era5; landsat-composite] |
| Skyfield, PyMC, Optuna, NumPy, pandas | Software | [skyfield; pymc; optuna] |

---

## 13. Change log

| Date | Change |
|---|---|
| 2026-09-30 | Initial draft |
| 2026-09-30 | §3.1, §3.4: Phase 1 implementation notes (strata cell list; stationary renewal start; hourly midpoint states) |
| 2026-10-01 | §3.3: fitted priors note (Rutherford, Omara, Sherwin 2024, Cusworth); §4.1 sensor parameters grounded in blind tests (see sensor YAMLs) |
| 2026-09-30 | §8: Phase 7 policy engine note |
| 2026-09-30 | §9: Phase 6 validation framework note |
| 2026-09-30 | §7: Phase 5 scoring conventions note |
| 2026-09-30 | §6.7, §7: interval is two-sided 90 % [p5, p95]; original percentiles marked superseded |
| 2026-10-01 | §4 table (field of view and pointing), §5.1 satellites (field of regard, scene footprint, incidental capture): DECISION_LOG "Incidental capture" |
| 2026-10-01 | §5.1 aircraft scheduling (regional campaigns), §8.1 Targeting wording corrected and Scheduling row: DECISION_LOG "Regional flight campaigns" |
| 2026-10-01 | §6.7 (quantile grid, prior summary, evidence counts persisted), §7 (decision-only certifiable/fails/indeterminate shares; precise, prior-only, evidence-ratio rows), §8.3 note: DECISION_LOG "certification is the compliance decision at 95 %" |
| 2026-09-30 | §6.7 estimand (realised-mass predictive), sampler, §6.3–6.5 v1 likelihood definitions, §10 measured timings, §11 limitation 10: Phase 4 |
| 2026-09-30 | §4.1 wind floor, §5.2 gate conventions: Phase 3 implementation notes |
| 2026-09-30 | §4: Phase 2 note — sensor YAML blocks carry `provenance.status` (fitted / summary / assumption); POD may be specified as POD50/POD90; see DECISION_LOG "Sensor library provenance scheme" |
