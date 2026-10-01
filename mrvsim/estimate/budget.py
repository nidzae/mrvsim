"""Variance budget by oracle ablation (TDD section 6.8) for one facility.

Each component's contribution is the reduction in the 90 % interval width of
M_hat when that error source is set to its oracle value, normalised so the
components sum to the total width. Diagnostic only; components interact.

| Component            | Oracle (as implemented)                                                              |
|----------------------|---------------------------------------------------------------------------------------|
| quantification       | every sensor's sigma -> 0 (floored at 0.05 for the importance sampler)                |
| temporal_sampling    | pi of included intermittent sources fixed at the true per-source pi; realisation noise off |
| detection_censoring  | sources below the most sensitive deployed sensor's POD10 revealed as exact rates      |
| spatial_completeness | observations re-simulated with every cloud/sun/wind gate forced usable                |
| false_calls          | lambda_FP -> 0 in simulation and likelihood                                           |
| denominator          | sigma_G -> 0                                                                          |
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from mrvsim.estimate.fast import Ablation, _weighted_percentiles, estimate_facility
from mrvsim.estimate.inputs import EstimatorInputs, build_inputs
from mrvsim.estimate.likelihood import SensorParams
from mrvsim.io.seeds import SeedTree
from mrvsim.observe.deployment import DeploymentPlan
from mrvsim.observe.simulator import ObservationSet, simulate_observations
from mrvsim.population.generate import Population
from mrvsim.sensors.library import SensorLibrary

COMPONENTS = ("quantification", "temporal_sampling", "detection_censoring", "spatial_completeness", "false_calls", "denominator")


@dataclass
class VarianceBudget:
    facility: int
    total_width: float
    reductions: dict[str, float]        # absolute width reduction per component (>= 0)
    shares: dict[str, float]            # normalised to sum to 1 (0 if total reduction is 0)
    widths: dict[str, float]            # interval width under each ablation
    meta: dict[str, Any]


def _width(i: int, inputs: EstimatorInputs, pbs, sparams, seeds: SeedTree, n_draws: int, ab: Ablation | None) -> float:
    mass, _, _, w, _, _ = estimate_facility(i, inputs, pbs, sparams, seeds, n_draws, ab)
    lo, hi = _weighted_percentiles(mass, w, (5, 95))
    return float(hi - lo)


def variance_budget(i: int, pop: Population, obs: ObservationSet, plan: DeploymentPlan, library: SensorLibrary, seeds: SeedTree,
                    n_draws: int = 10_000, inputs: EstimatorInputs | None = None) -> VarianceBudget:
    inputs = inputs or build_inputs(pop, obs, library, seeds)
    pbs = [pop.priors.for_cell(s.basin, s.facility_type) for s in pop.strata.strata]
    sparams = [SensorParams.from_sensor(library[k]) for k in inputs.sensor_keys]
    base = _width(i, inputs, pbs, sparams, seeds, n_draws, None)
    widths: dict[str, float] = {}

    widths["quantification"] = _width(i, inputs, pbs, sparams, seeds, n_draws, Ablation(sigma_quant=0.0))
    # temporal sampling: true pi of this facility's sources, by candidate index
    s0, s1 = pop.source_offset[i], pop.source_offset[i + 1]
    true_pi = np.ones(8); k = min(s1 - s0, 8); true_pi[:k] = pop.pi[s0:s0 + k]
    # the oracle knows both the duty cycles and the realised on-hours, so the predictive realisation noise is off too
    widths["temporal_sampling"] = _width(i, inputs, pbs, sparams, seeds, n_draws, Ablation(pi_fixed=true_pi, realised=False))
    # detection censoring: reveal sources below the deployed POD10
    deployed = [library[k] for k in inputs.sensor_keys]
    pod10 = min((s.pod.pod_quantile(0.1) for s in deployed), default=np.inf)
    small = [(float(q), 0.05) for q in pop.q_kg_h[s0:s1] if q < pod10]
    widths["detection_censoring"] = _width(i, inputs, pbs, sparams, seeds, n_draws, Ablation(extra_source_obs=small or None))
    # spatial completeness: re-simulate with gates forced usable
    obs_forced = simulate_observations(pop, library, plan, seeds, obs.year, force_usable=True)
    inputs_forced = build_inputs(pop, obs_forced, library, seeds)
    widths["spatial_completeness"] = _width(i, inputs_forced, pbs, sparams, seeds, n_draws, None)
    # false calls: re-simulate without false positives and drop the mixture term
    obs_nofp = simulate_observations(pop, library, plan, seeds, obs.year, no_false_positives=True)
    inputs_nofp = build_inputs(pop, obs_nofp, library, seeds)
    widths["false_calls"] = _width(i, inputs_nofp, pbs, sparams, seeds, n_draws, Ablation(lam_fp_zero=True))
    widths["denominator"] = _width(i, inputs, pbs, sparams, seeds, n_draws, Ablation(sigma_g_zero=True))

    red = {c: max(base - widths[c], 0.0) for c in COMPONENTS}
    tot = sum(red.values())
    shares = {c: (red[c] / tot if tot > 0 else 0.0) for c in COMPONENTS}
    return VarianceBudget(i, base, red, shares, widths, {"n_draws": n_draws, "pod10_deployed_kg_h": float(pod10),
                                                          "n_revealed_sources": len(small)})
