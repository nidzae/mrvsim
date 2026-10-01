"""Calibration mechanics on a small default-like sample (not V7, which needs fitted priors and R replications).

Because truth is generated from the estimator's prior, a correct likelihood must give ~90 % coverage of the
two-sided 90 % interval regardless of the prior's realism. This guards the likelihood implementation against
regressions; the Monte Carlo standard error at this sample size is ~0.03, hence the wide acceptance band.
"""

from __future__ import annotations

import numpy as np
import pytest

from mrvsim.estimate import build_inputs, run_fast_estimator
from mrvsim.io.seeds import SeedTree
from mrvsim.observe import build_plan, simulate_observations
from mrvsim.population import generate_population
from mrvsim.sensors import load_library

LIB = load_library()


@pytest.mark.slow
def test_default_policy_coverage_near_90_percent() -> None:
    modes = {s.key: (s.schedule, s.observation_mode, bool(s.orbit.tasked) if s.orbit else False) for s in LIB}
    seeds = SeedTree(2026)
    pop = generate_population({"n_per_stratum": 30}, seeds.child(rep=0))
    policy = {"sensors": {"bridger_gml": {"coverage": 1.0, "frequency_per_year": 2},
                          "ghgsat_c": {"coverage": 0.3, "frequency_per_year": 12, "targeting": "throughput"},
                          "cms_generic": {"coverage": 0.2, "targeting": "throughput"}}}
    plan = build_plan(policy, seeds, pop.n_facilities, pop.lon, pop.throughput.gas_mkt_m3_yr, modes, 2024)
    obs = simulate_observations(pop, LIB, plan, seeds.child(rep=0), 2024)
    inputs = build_inputs(pop, obs, LIB, seeds)
    sub = np.arange(0, pop.n_facilities, 9)          # ~210 facilities
    post = run_fast_estimator(inputs, pop.strata, pop.priors, LIB, seeds, n_draws=3000, facilities=sub)
    cov = post.covers(pop.true_mass_kg_yr())[sub].mean()
    cov_i = post.covers(pop.true_intensity(), "intensity")[sub].mean()
    assert 0.82 <= cov <= 0.97, cov
    assert 0.80 <= cov_i <= 0.97, cov_i
    bias = np.median(np.log(post.mass_kg_yr_p50[sub] / pop.true_mass_kg_yr()[sub]))
    assert abs(bias) < 0.15, bias
