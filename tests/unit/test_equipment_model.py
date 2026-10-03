"""Equipment-based leak model for real sites (TDD sections 3.2-3.4 as amended 2026-10-03) [rutherford2021]."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mrvsim.estimate.fast import facility_priors
from mrvsim.io.seeds import SeedTree
from mrvsim.population import generate_population, load_population
from mrvsim.population.equipment import (
    MAX_EXPECTED_SOURCES, OVERRIDE_KEYS, expected_duty_cycle, load_equipment_cells, site_prior_params,
)

BASE = {"nu_on": np.array([2.0]), "tau_on": np.array([1.0]), "nu_off": np.array([6.0]), "tau_off": np.array([1.2]),
        "sigma_nu_on": np.array([0.5]), "sigma_nu_off": np.array([1.0])}


@pytest.fixture(scope="module")
def cells():
    c = load_equipment_cells()
    assert c is not None and c.provenance == "FITTED"
    return c


def _expected_site_rate_kg_h(cells, cls, b, n) -> np.ndarray:
    return n * (cells.k_steady[cls, b] * cells.steady_mean_kg_h[cls, b] + cells.k_episodic[cls, b] * cells.episodic_mean_kg_h[cls, b])


def test_classification_follows_rutherford_rules(cells) -> None:
    mscf = 28.316847 * 365.25                       # m3 per year for 1 Mscf/day
    gas = np.array([0.0, 50 * mscf, 50 * mscf, 50 * mscf, 3000 * mscf])
    oil = np.array([365.25 * 5, 0.0, 365.25 * 0.2, 365.25 * 5, 0.0])     # bbl/yr
    cls, b = cells.classify(gas, oil, np.array([1, 1, 1, 1, 2]))
    assert cls.tolist() == [3, 0, 1, 2, 0]          # oil only, dry gas, gas with oil (GOR 250), oil with gas (GOR 10), dry gas
    assert b.tolist() == [2, 5, 5, 5, 8]            # 5 bbl/d -> oil bin 2; 50 Mscf/d -> gas bin 5; 1,500 Mscf/d per well -> bin 8


def test_site_parameters_conserve_expected_mass(cells) -> None:
    """Expected site emission = wells x per-well cell mean, for single wells and for pooled multi-well sites."""
    for cls, b, n in ((0, 0, 1), (0, 6, 1), (2, 6, 20), (3, 1, 3), (1, 9, 8)):
        base = {k: np.repeat(v, 1) for k, v in BASE.items()}
        p = site_prior_params(cells, np.array([cls]), np.array([b]), np.array([n]), base)
        k = p["lambda_k"][0] + 1.0
        assert 1.0 <= k <= MAX_EXPECTED_SOURCES
        steady = np.exp(p["mu_0"][0] + 0.5 * p["sigma_0"][0] ** 2)
        pi = expected_duty_cycle(BASE["nu_on"], BASE["tau_on"], p["nu_off"], BASE["tau_off"], BASE["sigma_nu_on"], BASE["sigma_nu_off"])[0]
        inter = np.exp(p["mu_1"][0] + 0.5 * p["sigma_1"][0] ** 2) * pi
        got = k * ((1 - p["p_intermittent"][0]) * steady + p["p_intermittent"][0] * inter)
        want = _expected_site_rate_kg_h(cells, cls, b, n)
        assert got == pytest.approx(want, rel=1e-6)


def test_emissions_rise_with_wells_and_productivity(cells) -> None:
    one, ten = (_expected_site_rate_kg_h(cells, 2, 4, n) for n in (1, 10))
    assert ten == pytest.approx(10 * one)
    by_bin = [_expected_site_rate_kg_h(cells, 2, b, 1) for b in range(10)]
    assert by_bin[9] > by_bin[4] > by_bin[0]


def test_population_uses_site_priors_and_matches_cell_means(cells) -> None:
    pop = generate_population({"n_per_stratum": 100}, SeedTree(5))
    real = pop.site_row >= 0
    assert pop.prior_overrides is not None and set(pop.prior_overrides) == set(OVERRIDE_KEYS)
    assert np.isfinite(pop.prior_overrides["lambda_k"][real]).all() and np.isnan(pop.prior_overrides["lambda_k"][~real]).all()
    sites = pop.strata.sites
    rows = pop.site_row[real]
    cls, b = cells.classify(sites.gas_m3_yr[rows], sites.oil_bbl_yr[rows], sites.n_wells[rows])
    want = _expected_site_rate_kg_h(cells, cls, b, sites.n_wells[rows]).sum()
    got = np.bincount(pop.src_facility, weights=pop.q_kg_h * pop.pi, minlength=pop.n_facilities)[real].sum()
    assert got == pytest.approx(want, rel=0.15)          # 5,400 sites; lognormal sampling noise
    stratum = generate_population({"n_per_stratum": 30, "leak_model": "stratum"}, SeedTree(5))
    assert stratum.prior_overrides is None


def test_estimator_prior_is_the_site_prior(tmp_path: Path) -> None:
    from mrvsim.estimate.inputs import EstimatorInputs
    pop = generate_population({"n_per_stratum": 30}, SeedTree(9))
    pbs = [pop.priors.for_cell(s.basin, s.facility_type) for s in pop.strata.strata]
    inp = EstimatorInputs.__new__(EstimatorInputs)
    inp.stratum_idx, inp.prior_overrides = pop.stratum_idx, pop.prior_overrides
    i = int(np.nonzero(pop.site_row >= 0)[0][0]); j = int(np.nonzero(pop.site_row < 0)[0][0])
    sp = facility_priors(i, inp, pbs)
    assert sp.lambda_k == pytest.approx(pop.prior_overrides["lambda_k"][i]) and sp.mu_0 == pytest.approx(pop.prior_overrides["mu_0"][i])
    assert sp.alpha == pbs[int(pop.stratum_idx[i])].alpha                  # untouched fields come from the stratum
    assert facility_priors(j, inp, pbs) is pbs[int(pop.stratum_idx[j])]     # midstream keeps the stratum prior
    pop.save(tmp_path / "population")
    back = load_population(tmp_path / "population")
    np.testing.assert_array_equal(back.prior_overrides["mu_1"], pop.prior_overrides["mu_1"])
