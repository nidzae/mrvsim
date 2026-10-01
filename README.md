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
