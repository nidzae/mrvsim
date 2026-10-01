# Vendored dependencies

## `ldar_sim/` — LDAR-Sim v4

- Upstream: https://github.com/LDAR-Sim/LDAR_Sim (citation key `[ldarsim-repo]`; paper `[fox2021]`)
- License: MIT, Copyright (C) 2018-2025 Thomas Fox, Mozhou Gao, Thomas Barchyn, Chris Hugenholtz. The upstream `LICENSE.txt` is kept unmodified inside the submodule.
- Pinned as a git submodule at the commit recorded in this repository's tree (upstream `master`, after tag v4.2.4, fetched 2026-09-30).
- Purpose in MRVSim: reference implementation and cross-check for the population/observation engine (DECISION_LOG 2026-09-30 "Fork LDAR-Sim v4 as the simulation engine"), plus reuse of its ERA5 download tooling and daylight calculator. See DECISION_LOG entry "Q4 resolved: LDAR-Sim v4 has no satellite scheduler" for what is and is not reusable.
- Any MRVSim modification to LDAR-Sim code lives in a fork branch inside the submodule and is listed here with its rationale. None yet.

To fetch after cloning MRVSim: `git submodule update --init --depth 50`.

## `feast/` — FEAST 3.1 (Fugitive Emissions Abatement Simulation Toolkit)

- Upstream: https://github.com/FEAST-SEDLab/FEAST_PtE, branch `FEAST_3.1` (citation keys `[feast-repo]`, paper `[kemp2016]`), linked from https://www.eemdl.utexas.edu/feast.
- License: MIT, Copyright (c) 2017 Chandler Kemp. `LICENSE.txt` kept unmodified inside the submodule.
- Pinned as a git submodule at the commit recorded in this repository's tree (fetched 2026-09-30).
- Purpose in MRVSim: independent cross-check of basin totals in validation test V2 (TDD §9) through `mrvsim.validate.feast_adapter`, which converts an MRVSim population into FEAST `Component`/`Site`/`GasField` objects and runs FEAST with no LDAR program. Not modified.
