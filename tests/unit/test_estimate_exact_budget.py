"""Exact (PyMC) path agreement with the fast path, and the variance budget (TDD sections 6.7-6.8)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_estimate import LIB, YEAR, _custom_population, _plan, _run  # noqa: E402

from mrvsim.estimate.budget import COMPONENTS, variance_budget
from mrvsim.estimate.exact import exact_posterior
from mrvsim.estimate.likelihood import SensorParams
from mrvsim.io.seeds import SeedTree


@pytest.mark.slow
def test_exact_matches_fast_on_steady_source() -> None:
    pop = _custom_population(505, q_kg_h=30.0, intermittent=False)
    plan = _plan(pop, aircraft_visits=20, cms=False)
    obs, inputs, post = _run(pop, plan, seed=3, n_draws=8000)
    sp_h = pop.priors.for_cell(pop.strata.strata[0].basin, pop.strata.strata[0].facility_type)
    sparams = [SensorParams.from_sensor(LIB[k]) for k in inputs.sensor_keys]
    i = 0
    ex = exact_posterior(i, inputs, sp_h, sparams, seed=11, draws=600, tune=600, chains=2)
    lo, med, hi = ex.percentiles["mass"]
    truth = pop.true_mass_kg_yr()[i]
    assert lo <= truth <= hi or abs(med - truth) / truth < 0.15
    # fast and exact medians agree within the quantification-limited precision of 20 passes (~10 %)
    assert abs(med - post.mass_kg_yr_p50[i]) / post.mass_kg_yr_p50[i] < 0.15, (med, post.mass_kg_yr_p50[i])
    assert ex.method == "exact-pymc" and ex.diagnostics["draws"] == 1200


def test_variance_budget_shares_sum_to_one() -> None:
    """Budget on a facility drawn from the default population (inside the prior), aircraft + CMS deployed everywhere."""
    from mrvsim.observe.simulator import simulate_observations
    from mrvsim.population import generate_population

    seeds = SeedTree(4)
    pop = generate_population({"n_per_stratum": 30}, seeds.child(rep=0))
    plan = _plan(pop, aircraft_visits=6, cms=True)
    obs = simulate_observations(pop, LIB, plan, seeds, YEAR)
    # a facility with at least one intermittent source and a non-trivial true mass
    cand = [i for i in range(pop.n_facilities) if pop.z[pop.source_offset[i]:pop.source_offset[i + 1]].any() and pop.true_mass_kg_yr()[i] > 1000]
    fid = cand[0]
    vb = variance_budget(fid, pop, obs, plan, LIB, seeds, n_draws=6000)
    assert set(vb.shares) == set(COMPONENTS)
    assert all(v >= 0 for v in vb.reductions.values())
    assert sum(vb.reductions.values()) > 0, vb.widths
    assert abs(sum(vb.shares.values()) - 1.0) < 1e-9
    assert vb.total_width > 0
