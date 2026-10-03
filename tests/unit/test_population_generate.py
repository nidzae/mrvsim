"""End-to-end population generation (TDD section 3) and reproducibility."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from mrvsim.io.config import RunConfig
from mrvsim.io.run import RunContext, compare_run_dirs
from mrvsim.io.seeds import SeedTree
from mrvsim.population import (
    PlaceholderPriorsError, generate_population, load_priors, load_strata, require_fitted,
)
from mrvsim.population.priors import DEFAULT_PRIORS_PATH


@pytest.fixture(scope="module")
def pop():
    return generate_population({"n_per_stratum": 30}, SeedTree(2024))


def test_shapes_and_csr(pop) -> None:
    n_fac = pop.n_facilities
    assert n_fac == pop.strata.sizes(30).sum()          # strata of real sites are capped at their site count
    assert pop.source_offset.shape == (n_fac + 1,)
    assert pop.source_offset[-1] == pop.n_total_sources
    assert np.all(pop.n_sources >= 1)
    np.testing.assert_array_equal(np.diff(pop.source_offset), pop.n_sources)
    np.testing.assert_array_equal(pop.src_facility, np.repeat(np.arange(n_fac), pop.n_sources))
    assert pop.states.n_sources == pop.n_total_sources


def test_steady_sources_always_on(pop) -> None:
    steady = pop.z == 0
    assert np.all(pop.pi[steady] == 1.0)
    assert np.all(pop.states.on_hours()[steady] == pop.n_hours)


def test_intermittent_duty_cycle_identity(pop) -> None:
    inter = pop.z == 1
    assert inter.any()
    realised = pop.states.on_hours()[inter] / pop.n_hours
    # Population-wide realised on-fraction should match mean pi within Monte Carlo error.
    assert abs(realised.mean() - pop.pi[inter].mean()) < 0.02


def test_truth_quantities_consistent(pop) -> None:
    m = pop.true_mass_kg_yr()
    assert m.shape == (pop.n_facilities,)
    assert np.all(m >= 0) and (m > 0).mean() > 0.9   # a site whose only source is very intermittent can emit nothing in a year
    # Realised mass should be close to the process expectation in aggregate.
    ratio = m.sum() / pop.expected_mass_kg_yr().sum()
    assert 0.9 < ratio < 1.1
    intensity = pop.true_intensity()
    assert np.all(np.isfinite(intensity)) and np.all(intensity >= 0)
    # Q_i(t) summed over all hours equals M_i.
    total = pop.facility_rate_at(np.arange(pop.n_hours)).sum(axis=1)
    np.testing.assert_allclose(total, m, rtol=1e-9)


def test_rate_at_pairs_matches_matrix(pop) -> None:
    fac = np.array([0, 3, 3, pop.n_facilities - 1])
    hrs = np.array([0, 5000, 5001, 8759])
    q_pairs = pop.facility_rate_at_pairs(fac, hrs)
    q_mat = pop.facility_rate_at(hrs)
    np.testing.assert_allclose(q_pairs, q_mat[fac, np.arange(4)])


def test_coordinates_inside_basin_box() -> None:
    pop = generate_population({"n_per_stratum": 30, "site_locations": "box"}, SeedTree(2024))
    assert pop.n_facilities == 30 * len(pop.strata) and not (pop.site_row >= 0).any()
    keys = list(pop.strata.basins)
    for bi, k in enumerate(keys):
        sel = pop.basin_idx == bi
        if not sel.any():
            continue
        b = pop.strata.basins[k]
        assert np.all((pop.lat[sel] >= b.lat_range[0]) & (pop.lat[sel] <= b.lat_range[1]))
        assert np.all((pop.lon[sel] >= b.lon_range[0]) & (pop.lon[sel] <= b.lon_range[1]))


def test_throughput_classes_are_ordered(pop) -> None:
    """Within a basin x type cell, class t1 < t2 < t3: in produced energy for real sites, in marketed gas otherwise."""
    c = pop.constants
    energy = pop.throughput.gas_mkt_m3_yr * c.gas_hhv_mj_per_m3 + pop.throughput.oil_bbl_yr * c.oil_mj_per_bbl
    for s_lo, s_hi in zip(pop.strata.strata[:-1], pop.strata.strata[1:]):
        if (s_lo.basin, s_lo.facility_type) != (s_hi.basin, s_hi.facility_type):
            continue
        size = energy if pop.strata.rows_of(s_lo.index) is not None else pop.throughput.gas_mkt_m3_yr
        lo, hi = size[pop.stratum_idx == s_lo.index], size[pop.stratum_idx == s_hi.index]
        assert lo.max() <= hi.min() * (1 + 1e-9)


def test_stratum_weights_sum_to_one(pop) -> None:
    assert pop.stratum_weights("count").sum() == pytest.approx(1.0)
    assert pop.stratum_weights("throughput").sum() == pytest.approx(1.0)


def test_placeholder_priors_are_refused_for_validation() -> None:
    strata = load_strata()
    priors = load_priors(DEFAULT_PRIORS_PATH, list(strata.basins), list(strata.facility_types))
    from mrvsim.population.priors import PLACEHOLDER_PRIORS_PATH
    ph = load_priors(PLACEHOLDER_PRIORS_PATH, list(strata.basins), list(strata.facility_types))
    assert ph.is_placeholder and not priors.is_placeholder
    with pytest.raises(PlaceholderPriorsError):
        require_fitted(ph, "validation test V1")


def test_quintile_strata_for_v4() -> None:
    strata5 = load_strata(n_classes=5)
    assert len(strata5) == len(load_strata()) // 3 * 5
    assert strata5.weights_count().sum() == pytest.approx(1.0)


def test_population_byte_identical_across_runs(tmp_path: Path) -> None:
    cfg = RunConfig.from_dict({"name": "pop", "seed": 777, "population": {"n_per_stratum": 30}})
    for rid in ("a", "b"):
        with RunContext(cfg, root=tmp_path, run_id=rid) as run:
            p = generate_population(cfg.population, run.seeds.child(rep=0))
            p.save(run.dir / "population")
            run.save_array("true_mass_kg_yr", p.true_mass_kg_yr())
    for name in ("facilities.npz", "sources.npz", "states.npz"):
        assert (tmp_path / "a" / "population" / name).read_bytes() == (tmp_path / "b" / "population" / name).read_bytes()
    res = compare_run_dirs(tmp_path / "a", tmp_path / "b")
    assert res["true_mass_kg_yr.npy"]


def test_memory_footprint_of_states(pop) -> None:
    """TDD section 10: bit-packed states are ~1/8 of a bool array."""
    assert pop.states.nbytes() <= pop.n_total_sources * pop.n_hours / 8 + pop.n_total_sources


def test_well_pads_are_real_sites(pop) -> None:
    """Well-pad facilities are distinct real sites with the site's location and production [ogim]."""
    sites = pop.strata.sites
    assert sites is not None and len(sites) > 500_000
    real = pop.site_row >= 0
    wp = np.isin(pop.ftype_idx, [list(pop.strata.facility_types).index(k) for k in ("wp_oil", "wp_mixed", "wp_gas")])
    np.testing.assert_array_equal(real, wp)
    rows = pop.site_row[real]
    assert np.unique(rows).size == rows.size
    np.testing.assert_array_equal(pop.lat[real], sites.lat[rows])
    np.testing.assert_array_equal(pop.throughput.oil_bbl_yr[real], sites.oil_bbl_yr[rows])
    np.testing.assert_allclose(pop.throughput.gas_mkt_m3_yr[real], np.maximum(sites.gas_m3_yr[rows], 1.0))
    assert np.isfinite(pop.true_intensity()).all()          # sites reporting no gas still have a finite intensity


def test_weights_reproduce_the_site_population() -> None:
    """Weighted sample shares match the full site table: small sites are neither over- nor under-weighted."""
    pop = generate_population({"n_per_stratum": 100}, SeedTree(7))
    sites, c = pop.strata.sites, pop.constants
    boe_d = lambda gas, oil: (oil + gas * c.gas_hhv_mj_per_m3 / c.oil_mj_per_bbl) / 365.0
    small_all = boe_d(sites.gas_m3_yr, sites.oil_bbl_yr) < 15.0
    real = pop.site_row >= 0
    small = boe_d(pop.throughput.gas_mkt_m3_yr, pop.throughput.oil_bbl_yr)[real] < 15.0
    wc, wt = pop.stratum_weights("count")[real], pop.stratum_weights("throughput")[real]
    assert abs(wc[small].sum() / wc.sum() - small_all.mean()) < 0.05
    assert abs(wt[small].sum() / wt.sum() - sites.gas_m3_yr[small_all].sum() / sites.gas_m3_yr.sum()) < 0.03
    assert pop.stratum_weights("count").sum() == pytest.approx(1.0) and pop.stratum_weights("throughput").sum() == pytest.approx(1.0)


def test_midstream_locations_from_pool(tmp_path: Path) -> None:
    """A midstream cell with a location pool sits on pool points; a file without a site table keeps lognormal throughput."""
    strata = load_strata(sites=None)
    rng = np.random.default_rng(0)
    pool = np.column_stack([rng.uniform(31.0, 32.0, 500), rng.uniform(-103.0, -102.0, 500)]).astype(np.float32)
    path = tmp_path / "sites.npz"
    np.savez(path, **{"pool:other/processing": pool})
    cfg = {"n_per_stratum": 30, "sites_path": str(path)}
    pop = generate_population(cfg, SeedTree(2024))
    b, f = strata.basin_index()["other"], strata.facility_type_index()["processing"]
    cell = (pop.basin_idx == b) & (pop.ftype_idx == f)
    pts = np.column_stack([pop.lat[cell], pop.lon[cell]])
    pool_set = {tuple(r) for r in pool.astype(float)}
    assert all(tuple(r) in pool_set for r in pts)
    box = generate_population({"n_per_stratum": 30, "site_locations": "box"}, SeedTree(2024))
    np.testing.assert_array_equal(pop.lat[~cell], box.lat[~cell])     # cells without a pool keep the box draw
    np.testing.assert_array_equal(pop.throughput.gas_mkt_m3_yr, box.throughput.gas_mkt_m3_yr)


def test_saved_population_keeps_its_strata(tmp_path: Path, pop) -> None:
    from mrvsim.population import load_population
    from mrvsim.population.strata import load_saved_strata
    pop.save(tmp_path / "population")
    back = load_population(tmp_path / "population")
    assert [t.key for t in back.strata.strata] == [t.key for t in pop.strata.strata]
    np.testing.assert_allclose(back.stratum_weights("throughput"), pop.stratum_weights("throughput"))
    assert len(load_saved_strata(tmp_path / "missing")) == 63        # runs saved before 2026-10-03
