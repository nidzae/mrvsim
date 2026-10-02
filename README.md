# MRVSim — methane MRV coverage simulator

An Observing System Simulation Experiment for US onshore oil and gas: synthetic facilities with known emissions, simulated sensors, a Bayesian estimator, and scoring of the resulting uncertainty intervals against truth.

- What and why: `docs/PRD.md`
- How every quantity is computed: `docs/TECHNICAL_DESIGN.md`
- Decisions: `docs/DECISION_LOG.md` · Citations: `docs/REFERENCES.md` · User guide: `docs/QUICKSTART.md`
- Build log: `docs/status/`

## Setup

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"
git submodule update --init --depth 50      # LDAR-Sim v4 (MIT) into vendor/ldar_sim
.venv/bin/python -m pytest tests -q
```

Every run takes a seed and writes `runs/<id>/` with its config, seed, git SHA, and elapsed time (PRD N4). Two runs with the same config and seed are byte-identical.

## Run the app

```bash
.venv/bin/uvicorn mrvsim.api.server:app --port 8000        # API + built front end at http://127.0.0.1:8000
cd web && npm install && npm run build                      # rebuild the front end after changes (or `npm run dev` for live reload on :5173)
export ANTHROPIC_API_KEY=...                                # optional: enables the Gap-analysis chat (never put a key in code)
```

Validation: `MRVSIM_ALLOW_PLACEHOLDER_VALIDATION=1 .venv/bin/python -m mrvsim.validate.runner --quick` (diagnostic only until priors are fitted).

## Conventions (2026-10-01)

- **Certification is a compliance decision at 95 %:** certified iff p95 ≤ bar, fails iff p5 > bar, indeterminate iff the 90 % interval straddles it (PRD §5.3 as amended). Precision (relative half-width w against w_max) and evidence (posterior width as a fraction of the prior's; "prior-only" when the data barely moved it) are reported alongside and never block a decision (PRD §5.4, §5.4a).
- **Sensor assignment** is per facility by coverage share and targeting (`random`, or `throughput` = top-k by marketed gas); location never decides assignment. Aircraft can fly **regional campaigns** (`scheduling: campaign`, `campaign_days`), and a tasked satellite scene or aircraft survey block also captures the neighbours inside it (**incidental capture**, `incidental_capture: true`). Tasked satellites are reachable on any pass within their pointing field of regard (TDD §5.1).
- The map's drill-down shows both KPIs, the posterior probability of being below the bar, the prior interval, the monitoring rules that selected (or did not select) the facility, and the observation timeline with incidental looks as hollow markers. See `docs/QUICKSTART.md`; decisions in `docs/DECISION_LOG.md`.
