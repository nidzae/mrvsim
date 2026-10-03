"""Estimator tests (TDD section 6; CLAUDE.md Phase 4 acceptance tests)."""

from __future__ import annotations

import copy

import numpy as np
import pytest

from mrvsim.estimate import build_inputs, run_fast_estimator
from mrvsim.estimate.likelihood import K_MAX, SensorParams, enumerate_states, snapshot_nd_loglik
from mrvsim.io.seeds import SeedTree
from mrvsim.observe.deployment import DeploymentPlan, SensorDeployment
from mrvsim.observe.simulator import simulate_observations
from mrvsim.population import generate_population, load_strata
from mrvsim.population.temporal import StatePaths, simulate_intermittent_states
from mrvsim.sensors import load_library

LIB = load_library()
YEAR = 2024
N_FAC = 24


def _custom_population(seed: int, q_kg_h: float, intermittent: bool, nu_on: float = 2.0, nu_off: float = 4.5, tau: float = 1.0):
    """Population of N_FAC single-source facilities in stratum 0 with a prescribed source."""
    base = generate_population({"n_per_stratum": 30, "leak_model": "stratum"}, SeedTree(seed))
    p = copy.copy(base)
    idx = np.arange(N_FAC)
    for name in ("stratum_idx", "basin_idx", "ftype_idx", "tclass_idx", "lat", "lon"):
        setattr(p, name, getattr(base, name)[idx])
    p.n_sources = np.ones(N_FAC, np.int64); p.source_offset = np.arange(N_FAC + 1); p.src_facility = np.arange(N_FAC)
    p.z = np.full(N_FAC, 1 if intermittent else 0, np.int8); p.q_kg_h = np.full(N_FAC, q_kg_h)
    p.nu_on = np.full(N_FAC, nu_on); p.tau_on = np.full(N_FAC, tau); p.nu_off = np.full(N_FAC, nu_off); p.tau_off = np.full(N_FAC, tau)
    if intermittent:
        from mrvsim.population.temporal import duty_cycle
        p.pi = duty_cycle(p.nu_on, p.tau_on, p.nu_off, p.tau_off)
        states = simulate_intermittent_states(np.random.default_rng(seed), p.nu_on, p.tau_on, p.nu_off, p.tau_off)
    else:
        p.pi = np.ones(N_FAC); states = np.ones((N_FAC, 8760), dtype=bool)
    p.states = StatePaths.from_bool(states)
    cond = copy.copy(base.conditions)
    for name in ("p_cloud", "surface_reflectance", "surface_heterogeneity", "wind_k", "wind_lambda"):
        setattr(cond, name, getattr(base.conditions, name)[idx])
    p.conditions = cond
    thr = copy.copy(base.throughput)
    for name in ("gas_mkt_m3_yr", "oil_bbl_yr", "x_ch4", "g_ch4_kg_yr", "f_gas", "mmbtu_yr", "ghgrp_reporter"):
        setattr(thr, name, getattr(base.throughput, name)[idx])
    p.throughput = thr
    return p


def _plan(pop, aircraft_visits: int, cms: bool) -> DeploymentPlan:
    plan = DeploymentPlan(year=YEAR)
    facs = np.arange(pop.n_facilities)
    if aircraft_visits:
        rng = np.random.default_rng(0)
        hours = (np.sort(rng.choice(np.arange(24, 8736, 24), size=aircraft_visits, replace=False))[None, :] + 18)  # ~noon central
        plan.deployments["bridger_gml"] = SensorDeployment("bridger_gml", facs, np.tile(hours, (facs.size, 1)).ravel(), np.repeat(facs, aircraft_visits))
    if cms:
        plan.deployments["cms_generic"] = SensorDeployment("cms_generic", facs)
    return plan


def _run(pop, plan, seed: int, n_draws: int = 4000):
    seeds = SeedTree(seed)
    obs = simulate_observations(pop, LIB, plan, seeds, YEAR)
    inputs = build_inputs(pop, obs, LIB, seeds)
    post = run_fast_estimator(inputs, pop.strata, pop.priors, LIB, seeds, n_draws=n_draws)
    return obs, inputs, post


def test_enumeration_matches_brute_force() -> None:
    rng = np.random.default_rng(0)
    n = 50
    K = rng.integers(1, K_MAX + 1, size=n)
    omega = (np.arange(K_MAX)[None, :] < K[:, None]).astype(float)
    q = np.exp(rng.normal(1, 1, (n, K_MAX))); pi = rng.uniform(0.05, 0.95, (n, K_MAX))
    en = enumerate_states(q, pi, omega)
    # weights sum to one per draw, and every draw is in exactly one group
    for sel, Q, logw in en.groups:
        np.testing.assert_allclose(np.exp(logw).sum(axis=1), 1.0, atol=1e-10)
    assert sorted(np.concatenate([g[0] for g in en.groups]).tolist()) == list(range(n))
    # brute force expectation of a test function vs. enumeration
    sp = SensorParams.from_sensor(LIB["bridger_gml"])
    ll = snapshot_nd_loglik(en, sp, wind=3.0, rho=0.3, count=1, prune_tol=0.0)
    for d in range(5):
        k = K[d]; states = (np.arange(2**k)[:, None] >> np.arange(k)[None, :]) & 1
        Qs = states @ q[d, :k]
        w = np.prod(np.where(states == 1, pi[d, :k], 1 - pi[d, :k]), axis=1)
        expect = np.sum(w * (1 - sp.pod(Qs * sp.q_factor(3.0, 0.3))))
        assert np.isclose(ll[d], np.log(expect), atol=1e-9)


def test_steady_source_20_aircraft_passes() -> None:
    """CLAUDE.md Phase 4: one steady source, 20 aircraft passes -> median q within 10 %, 90 % interval contains truth."""
    pop = _custom_population(101, q_kg_h=30.0, intermittent=False)
    obs, inputs, post = _run(pop, _plan(pop, aircraft_visits=20, cms=False), seed=7, n_draws=6000)
    truth = pop.true_mass_kg_yr()
    rel_err = np.abs(post.mass_kg_yr_p50 - truth) / truth
    covered = post.covers(truth)
    # population-level statements: median error within 10 % for most facilities; the 90 % interval covers ~90 %
    assert np.median(rel_err) < 0.10, np.median(rel_err)
    assert covered.mean() >= 0.80, covered.mean()
    assert np.all(post.ess > 30)
    # intensity interval is consistent with the noisy denominator
    assert np.all(post.intensity_p10 < post.intensity_p50) and np.all(post.intensity_p50 < post.intensity_p90)
    # evidence (PRD section 5.4a): 20 passes narrow the interval well below the prior width; counts are recorded
    ratio = post.evidence_ratio("mass")
    assert np.all(np.isfinite(ratio)) and np.median(ratio) < 0.5, np.median(ratio)
    assert post.evidence is not None and np.all(post.evidence[0] > 0) and np.all(post.evidence[0] <= 20)
    # the quantile grid is monotone and consistent with the stored percentiles; P(K <= p95) = 0.95
    assert np.all(np.diff(post.mass_quantiles, axis=0) >= 0)
    assert np.allclose(post.mass_quantiles[95], post.mass_kg_yr_p95)
    assert np.allclose([post.prob_below(post.mass_kg_yr_p95[i], "mass")[i] for i in range(3)], 0.95, atol=1e-6)


def test_intermittent_identifiability_cms_narrows_pi() -> None:
    """PRD section 7.4 / CLAUDE.md Phase 4: without CMS the interval on pi is wide; adding CMS narrows it.

    The truth's duration parameters are taken from the stratum prior (so truth lies inside the prior); the
    duty cycle is made larger than the prior median by shortening the off-period, which is what CMS must detect.
    """
    base = generate_population({"n_per_stratum": 30, "leak_model": "stratum"}, SeedTree(202))
    sp = base.priors.for_cell(base.strata.strata[0].basin, base.strata.strata[0].facility_type)
    nu_off_truth = sp.nu_off - 1.5          # ~4.5x shorter off-periods than the prior median: pi well above the prior
    pop = _custom_population(202, q_kg_h=60.0, intermittent=True, nu_on=sp.nu_on, nu_off=nu_off_truth, tau=sp.tau_on)
    _, _, post_no = _run(pop, _plan(pop, aircraft_visits=4, cms=False), seed=8, n_draws=4000)
    _, _, post_cms = _run(pop, _plan(pop, aircraft_visits=4, cms=True), seed=8, n_draws=4000)
    w_no = np.median(post_no.pi_mean_p90 - post_no.pi_mean_p10)
    w_cms = np.median(post_cms.pi_mean_p90 - post_cms.pi_mean_p10)
    truth_pi = float(pop.pi.mean())        # single source per facility: rate-weighted duty cycle equals pi
    assert w_no > 0.4 * truth_pi, (w_no, truth_pi)                 # without CMS the pi interval stays comparable to the quantity itself
    assert w_cms < w_no, (w_no, w_cms)                              # CMS narrows the duty-cycle interval
    # CMS narrows the mass interval materially and pins the mass: facility-level CMS summaries do not separate one source
    # at pi = 0.07 from several sources at pi = 0.02 (same mass, detected fraction and rates), so the identifiability
    # statement of PRD section 7.4 is tested on the annual mass, which they do pin (TDD section 6.4 v2 HMM would add the rest).
    w_mass_no = np.median(post_no.relative_half_width("mass")); w_mass_cms = np.median(post_cms.relative_half_width("mass"))
    assert w_mass_cms < 0.7 * w_mass_no, (w_mass_no, w_mass_cms)
    # Tier C CMS quantification (sigma = 0.9) lets the prior pull a 60 kg/h source toward the prior median, so the median
    # is within a factor of 2 (not 1.5) of truth; the interval still covers it.
    ratio = post_cms.mass_kg_yr_p50 / pop.true_mass_kg_yr()
    assert np.median(np.abs(np.log(ratio))) < np.log(2.0), np.median(ratio)
    assert post_cms.covers(pop.true_mass_kg_yr()).mean() >= 0.75


def test_no_observations_returns_prior() -> None:
    pop = _custom_population(303, q_kg_h=5.0, intermittent=False)
    plan = DeploymentPlan(year=YEAR)   # nothing deployed
    seeds = SeedTree(9)
    obs = simulate_observations(pop, LIB, plan, seeds, YEAR)
    inputs = build_inputs(pop, obs, LIB, seeds)
    post = run_fast_estimator(inputs, pop.strata, pop.priors, LIB, seeds, n_draws=2000)
    assert np.allclose(post.ess, 2000)                           # uniform weights
    assert np.all(post.mass_kg_yr_p90 > post.mass_kg_yr_p10 * 5)   # prior is wide
    # with nothing observed the posterior is the prior: evidence ratio 1 on common random numbers, zero evidence counts
    assert np.allclose(post.evidence_ratio("mass"), 1.0) and np.allclose(post.evidence_ratio("intensity"), 1.0)
    assert np.all(post.evidence == 0)


def test_fast_estimator_reproducible() -> None:
    pop = _custom_population(404, q_kg_h=20.0, intermittent=False)
    plan = _plan(pop, aircraft_visits=3, cms=False)
    _, _, a = _run(pop, plan, seed=5, n_draws=1000)
    _, _, b = _run(pop, plan, seed=5, n_draws=1000)
    np.testing.assert_array_equal(a.mass_kg_yr_p50, b.mass_kg_yr_p50)
    np.testing.assert_array_equal(a.ess, b.ess)
