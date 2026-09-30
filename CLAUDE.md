# CLAUDE.md — Build instructions for MRVSim

You are building MRVSim, a methane MRV coverage simulator. Read these before doing anything:

1. `docs/PRD.md` — what we are building and why. Requirements F1–F14, N1–N5.
2. `docs/TECHNICAL_DESIGN.md` — exactly how every quantity is computed. Section numbers are referenced below as "TDD §x".
3. `docs/DECISION_LOG.md` — decisions already made. Do not relitigate them; if implementation forces a change, add a new entry.
4. `docs/REFERENCES.md` — citation keys. Every parameter you write must reference one.

## Document maintenance rules (non-negotiable)

- **The docs are the spec.** When implementation reveals that the PRD or TDD is wrong, ambiguous, or infeasible, do not silently diverge. Do the following in the same commit:
  1. Add a `DECISION_LOG.md` entry (date, decision, reason, alternatives, docs updated).
  2. Edit the affected PRD or TDD section.
  3. Append a row to that document's change log table.
- **Never delete PRD requirements or TDD equations.** Mark them superseded with a reference to the decision-log entry.
- **Open questions** (PRD §10): when you resolve one, record the answer in the table and in the decision log.
- **Attribution:** every numeric parameter, prior, POD curve, dataset, and dependency gets a `[key]` from `REFERENCES.md` in its docstring or YAML config entry. If you need a source that is not in `REFERENCES.md`, add it with status `verify` and tell the user.
- **Do not invent DOIs or citations.** If you cannot confirm one, leave status `verify`.
- Keep `docs/QUICKSTART.md` in sync with the UI. If you rename a control or panel, update the guide.

## Repository layout

```
mrvsim/
  population/     TDD §3   stratification, source generation, temporal process, conditions
  sensors/        TDD §4   sensor library (YAML per sensor), POD fitting, error models
  observe/        TDD §5   observation opportunities, gates, detection, logging
  estimate/       TDD §6   likelihoods, priors, importance sampler (fast), PyMC model (exact), variance budget
  score/          TDD §7   calibration, width, certifiability, completeness, cost
  policy/         TDD §8   policy parameters, tip-and-cue rules, Optuna optimizer
  io/             config loading, run persistence, seeds
  api/            FastAPI server exposing run / score / drilldown / attribution endpoints
web/              React front end: map view, dashboard, gap-analysis panel, quick start, attribution, validation
tests/
  unit/
  validation/     V1–V7 as pytest modules (TDD §9)
configs/
  default.yaml    default stratified sample and default sensor mix
  sensors/*.yaml  one file per sensor class
  priors/*.yaml   fitted stratum hyperparameters, each with citation keys and fit provenance
data/
  raw/            downloaded public data (git-ignored); download scripts in data/scripts/
  fitted/         fitted priors and POD parameters with provenance JSON
docs/             PRD, TDD, DECISION_LOG, REFERENCES, QUICKSTART
vendor/ldar_sim/  fork of LDAR-Sim v4 (MIT); keep upstream license and attribution
```

## Build order

Work in this order. Each phase ends with passing tests and a short note to the user summarizing what was built, what changed in the docs, and what is blocked.

### Phase 0 — Scaffold and dependencies
- Python 3.11+, `numpy`, `scipy`, `pandas`, `pymc`, `skyfield`, `optuna`, `pyyaml`, `fastapi`, `pytest`. Front end: React + Vite, a map library (MapLibre or Leaflet), a chart library (Recharts or Plotly).
- Clone LDAR-Sim v4 into `vendor/ldar_sim/`. Read its `README` and code to answer PRD open question Q4 (orbit-based vs. cadence satellite scheduling). Record the answer.
- Seed handling in `mrvsim/io`: every run takes a seed; every random draw derives from it. Test: two runs with the same config and seed produce byte-identical outputs.

### Phase 1 — Population generator (TDD §3)
- Implement strata, source count, rate distribution with Pareto splice, alternating renewal process, throughput, and conditions.
- Fitted hyperparameters live in `configs/priors/`. Until real fits exist, ship placeholder values clearly marked `provenance: PLACEHOLDER` and refuse to run validation tests against placeholders.
- Data download scripts for MODIS cloud, ERA5 wind, and public throughput; document what could not be fetched.
- Tests: distribution shapes, duty-cycle identity π = E[D_on]/(E[D_on]+E[D_off]), bit-packed state storage round trip.

### Phase 2 — Sensor library (TDD §4)
- One YAML per sensor class with all fields in TDD §4 and citation keys. Validation tier is mandatory.
- POD fitting utility: given a table of (released rate, detected yes/no, wind, surface), fit logistic (a, b) by maximum likelihood and store the parameter posterior (Laplace approximation is fine).
- Start with the sensors that have Tier A data. Put Tier B/C sensors in with `tier` set and `enabled_for_certification: false`.
- Tests: POD monotone in q; quantification error reproduces the published 95% range for at least one aircraft sensor.

### Phase 3 — Observation simulator (TDD §5)
- Satellite overpasses via Skyfield from public TLEs; cache per satellite per year.
- Gates and detection exactly as TDD §5.2–5.3. Non-detections must be logged with conditions.
- Tests: a facility with a 10 t/h steady source is detected by TROPOMI on nearly every clear overpass; a 5 kg/h source is never detected by any satellite; a CMS log has 8,760 rows.

### Phase 4 — Estimator (TDD §6)
- Implement the fast path first (importance sampling from the prior, 10⁴ draws), then the exact PyMC model.
- Snapshot likelihoods with the exact 2^K enumeration; CMS run-length likelihood; ground-survey per-source likelihood; denominator likelihood.
- Output: posterior samples of M and I per facility, plus percentiles.
- Variance budget via oracle ablation.
- Tests: on a facility with one steady source and 20 aircraft passes, the posterior median of q is within 10% of truth and the 90% interval contains it; on a facility with an intermittent source and no CMS, the interval on π is wide; adding a CMS narrows it. This last test is the identifiability argument of PRD §7.4 and must pass.

### Phase 5 — Scoring (TDD §7)
- All metrics with Monte Carlo standard errors. Stratum weighting for throughput-based shares.
- Cost model reads per-sensor costs from the sensor YAML.

### Phase 6 — Validation tests V1–V7 (TDD §9)
- Each as a pytest module reading the published comparison values from `data/fitted/validation_targets.yaml` (with citation keys).
- V2 requires running FEAST 3.1 on the same population; write an adapter.
- Do not mark a validation test as passing against placeholder priors.

### Phase 7 — Policy engine and optimizer (TDD §8)
- Policy as a dataclass serialized to YAML; tip-and-cue rules evaluated daily in the observation loop.
- Optuna study with reduced-R trials and full-R re-scoring of the top 10. Output the Pareto set.

### Phase 8 — API and front end
- Endpoints: `POST /run`, `GET /run/{id}/summary`, `GET /run/{id}/facility/{fid}`, `GET /run/{id}/attribution`, `GET /validation`, `POST /optimize`.
- Views per PRD F9–F13:
  - **Map:** three-state facility coloring (certified / fails / indeterminate) at the user's (B, w_max); drill-down with interval bar against the bar line, observation timeline (detections, non-detections, cloud-outs), and variance-budget bar chart.
  - **Dashboard:** headline metrics; Pareto frontier (cost vs. w at fixed B); tornado chart (one-at-a-time sensor changes); slice tables.
  - **Gap analysis panel:** chat with the Anthropic API; put the current run's summary, variance budget, tornado, and slice tables in the system prompt; the model proposes a policy YAML; "Apply" button loads it. Use the API pattern in the artifacts documentation (no API key in code).
  - **Quick start:** a "?" button on every screen opens `docs/QUICKSTART.md` rendered as Markdown.
  - **Attribution panel:** every citation key that contributed to the displayed run, resolved against `REFERENCES.md`, with `verify`-status entries visibly flagged.
  - **Validation panel:** V1–V7 pass/fail with the compared values.
- Interactive recompute must meet PRD N3 (< 60 s) using the fast estimator and R = 20.

## Conventions

- Type hints everywhere; `numpy` vectorization over facilities; no per-facility Python loops in the hot path.
- Units in variable names where ambiguous: `rate_kg_h`, `mass_kg_yr`, `throughput_mmbtu`.
- Config over code: anything a user might change is YAML.
- Log every run's config, seed, git SHA, and elapsed time to `runs/<id>/`.
- When a computation deviates from a TDD equation for numerical reasons (log-space, clipping), comment the deviation with the TDD section number.

## When you are unsure

- If a TDD equation seems wrong, say so in your summary with the section number and your proposed fix; implement the TDD version behind a flag until the user decides.
- If a dataset cannot be downloaded from inside the environment, generate synthetic placeholders marked `PLACEHOLDER` and list exactly what the user needs to fetch.
- If a published number is needed and you are not confident of it, do not guess; leave a `TODO(verify)` with the citation key.

## Reporting to the user

At the end of each phase, write `docs/status/<date>-phase-<n>.md` with: what was built, tests passing, doc sections changed (with decision-log entry IDs), open questions resolved or added, and what is blocked.
