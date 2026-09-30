# Vendored dependencies

## `ldar_sim/` — LDAR-Sim v4

- Upstream: https://github.com/LDAR-Sim/LDAR_Sim (citation key `[ldarsim-repo]`; paper `[fox2021]`)
- License: MIT, Copyright (C) 2018-2025 Thomas Fox, Mozhou Gao, Thomas Barchyn, Chris Hugenholtz. The upstream `LICENSE.txt` is kept unmodified inside the submodule.
- Pinned as a git submodule at the commit recorded in this repository's tree (upstream `master`, after tag v4.2.4, fetched 2026-09-30).
- Purpose in MRVSim: reference implementation and cross-check for the population/observation engine (DECISION_LOG 2026-09-30 "Fork LDAR-Sim v4 as the simulation engine"), plus reuse of its ERA5 download tooling and daylight calculator. See DECISION_LOG entry "Q4 resolved: LDAR-Sim v4 has no satellite scheduler" for what is and is not reusable.
- Any MRVSim modification to LDAR-Sim code lives in a fork branch inside the submodule and is listed here with its rationale. None yet.

To fetch after cloning MRVSim: `git submodule update --init --depth 50`.
