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
