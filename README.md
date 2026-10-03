# MRVSim — methane MRV coverage simulator

An Observing System Simulation Experiment for US onshore oil and gas: synthetic facilities with known emissions, simulated sensors, a Bayesian estimator, and scoring of the resulting uncertainty intervals against truth.

- What and why: `docs/PRD.md`
- How every quantity is computed: `docs/TECHNICAL_DESIGN.md`
- Decisions: `docs/DECISION_LOG.md` · Citations: `docs/REFERENCES.md` · User guide: `docs/QUICKSTART.md`
- Build log: `docs/status/`

## Run it in 5 minutes

You need Python 3.11 or newer and git. Node.js is not needed: the built front end is committed in `web/dist`.

```bash
git clone https://github.com/nidzae/mrvsim && cd mrvsim
python3.11 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/uvicorn mrvsim.api.server:app --port 8000
```

On Windows use `py -3.11 -m venv .venv` and `.venv\Scripts\pip`, `.venv\Scripts\uvicorn`.

Open http://127.0.0.1:8000. The run selector starts empty (runs are stored locally in `runs/`, not in the repository): press **Run** under Sensors in Quick mode. The first run takes a few minutes because the satellite overpasses are computed and cached; later Quick runs take about 2 minutes. The **?** button on every screen opens the user guide (`docs/QUICKSTART.md`).

The Gap-analysis chat is optional and needs your own key: `export ANTHROPIC_API_KEY=...` before starting the server (never put a key in code). Everything else works without it.

## Development

```bash
.venv/bin/pip install -e ".[dev]"
git submodule update --init --depth 50      # LDAR-Sim v4 and FEAST 3.1 (both MIT) into vendor/; needed for validation V2 only
.venv/bin/python -m pytest tests/unit -q    # about 15 minutes
(cd web && npm install && npm run build)    # rebuild web/dist after front-end changes and commit it (or `npm run dev` for live reload on :5173)
```

Every run takes a seed and writes `runs/<id>/` with its config, seed, git SHA, and elapsed time (PRD N4). Two runs with the same config and seed are byte-identical.

Validation: `.venv/bin/python -m mrvsim.validate.runner --quick` writes `runs/validation/<timestamp>.json` for the Validation panel (refused against PLACEHOLDER priors unless `MRVSIM_ALLOW_PLACEHOLDER_VALIDATION=1`, which marks the results diagnostic only).

## Conventions (2026-10-01)

- **Certification is a compliance decision at 95 %:** certified iff p95 ≤ bar, fails iff p5 > bar, indeterminate iff the 90 % interval straddles it (PRD §5.3 as amended). Precision (relative half-width w against w_max) and evidence (posterior width as a fraction of the prior's; "prior-only" when the data barely moved it) are reported alongside and never block a decision (PRD §5.4, §5.4a).
- **Sensor assignment** is per facility by coverage share and targeting (`random`, or `throughput` = top-k by marketed gas); location never decides assignment. Aircraft can fly **regional campaigns** (`scheduling: campaign`, `campaign_days`), and a tasked satellite scene or aircraft survey block also captures the neighbours inside it (**incidental capture**, `incidental_capture: true`). Tasked satellites are reachable on any pass within their pointing field of regard (TDD §5.1).
- The map's drill-down shows both KPIs, the posterior probability of being below the bar, the prior interval, the monitoring rules that selected (or did not select) the facility, and the observation timeline with incidental looks as hollow markers. See `docs/QUICKSTART.md`; decisions in `docs/DECISION_LOG.md`.
