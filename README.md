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
