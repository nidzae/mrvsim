"""Observation-only bounds (PRD section 5.3a): what the measurements alone establish."""

from __future__ import annotations

import numpy as np
import pytest

from mrvsim.io.config import RunConfig
from mrvsim.io.seeds import SeedTree
from mrvsim.observe.simulator import CMSLog, ObservationSet
from mrvsim.pipeline import run_replication
from mrvsim.score.observed import HOURS, Z_95, observed_bounds
from mrvsim.sensors import load_library

LIB = load_library()


@pytest.fixture(scope="module")
def res():
    cfg = RunConfig.from_dict({"seed": 21, "replications": 1, "population": {"n_per_stratum": 30},
                               "policy": {"sensors": {"bridger_gml": {"coverage": 1.0, "frequency_per_year": 2},
                                                      "cms_generic": {"coverage": 0.3, "targeting": "throughput"}}},
                               "estimator": {"n_draws": 300}})
    return run_replication(cfg, SeedTree(cfg.seed), 0, LIB, n_draws=300, facilities_per_stratum=1)


def test_snapshots_alone_decide_nothing(res) -> None:
    """A facility with aircraft passes but no monitor is unobserved in time: nothing measured, no upper limit, no verdict."""
    ob, pop = res.observed, res.pop
    monitored = np.zeros(pop.n_facilities, dtype=bool); monitored[res.obs.cms["cms_generic"].facilities] = True
    none = ~monitored
    assert np.all(ob.observed_hours[none] == 0) and np.all(ob.mass_lo_kg_yr[none] == 0)
    assert np.all(np.isinf(ob.mass_hi_kg_yr[none])) and np.all(np.isinf(ob.intensity_hi[none]))
    assert np.all(ob.states("intensity", 0.002)[none] == 2) and np.all(ob.states("mass", 50e3)[none] == 2)
    assert ob.snapshot_detections.sum() > 0                                                                  # detections are kept as flags
    # real monitors have outages and wind gaps, so no monitored site was observed all year: none is certified either
    assert np.all(ob.observed_hours < HOURS) and not np.any(ob.states("mass", 50e3) == 0)


def test_bounds_follow_the_monitor_log(res) -> None:
    ob, c = res.observed, res.obs.cms["cms_generic"]
    s = LIB["cms_generic"]; sigma = s.quantification.sigma; limit = s.pod.pod90_kg_h
    j = int(np.argmax((c.usable & c.detected).sum(axis=1))); f = int(c.facilities[j])
    det = c.usable[j] & c.detected[j]
    measured = float(np.nansum(np.where(det, c.reported_kg_h[j], 0.0)))
    assert ob.observed_hours[f] == c.usable[j].sum()
    assert ob.mass_lo_kg_yr[f] == pytest.approx(measured * np.exp(-Z_95 * sigma))
    assert ob.mass_hi_observed_kg_yr[f] == pytest.approx(measured * np.exp(Z_95 * sigma) + (c.usable[j] & ~c.detected[j]).sum() * limit)
    assert ob.mass_hi_kg_yr[f] >= ob.mass_hi_observed_kg_yr[f] >= ob.mass_lo_kg_yr[f]


def test_certification_needs_the_whole_year() -> None:
    """A monitor that sees nothing all year certifies a site below its detection limit; the same monitor with gaps does not."""
    s = LIB["cms_generic"]; limit = s.pod.pod90_kg_h
    n = 2
    usable = np.ones((n, HOURS), dtype=bool); usable[1, : HOURS // 5] = False            # second site: 20 % of the year unobserved
    quiet = CMSLog("cms_generic", np.arange(n), usable, np.zeros((n, HOURS), dtype=bool), np.full((n, HOURS), np.nan, np.float32),
                   np.zeros((n, HOURS), np.float32))
    from mrvsim.observe.simulator import ObservationLog
    g = np.full(n, 5.0e6); f_gas = np.ones(n)                                              # 5,000 t CH4 handled per year
    ob = observed_bounds(ObservationSet(2024, ObservationLog(), {"cms_generic": quiet}, {}, {}), LIB, n, g, f_gas, np.full(n, 1e7))
    assert ob.mass_hi_kg_yr[0] == pytest.approx(HOURS * limit) and ob.mass_lo_kg_yr[0] == 0
    assert ob.states("mass", 50e3).tolist() == [0, 2]                                      # 39 t/yr ceiling certifies at 50 t/yr; the gap does not
    assert np.isinf(ob.mass_hi_kg_yr[1]) and np.isfinite(ob.mass_hi_observed_kg_yr[1])
    capped = observed_bounds(ObservationSet(2024, ObservationLog(), {"cms_generic": quiet}, {}, {}), LIB, n, g, f_gas, np.full(n, 1e7), cap="throughput")
    assert capped.intensity_hi[1] == pytest.approx(capped.intensity_hi_observed[1] + (HOURS // 5) / HOURS, rel=1e-6)   # optional cap: unobserved share of throughput
    loud = CMSLog("cms_generic", np.arange(1), np.ones((1, HOURS), bool), np.ones((1, HOURS), bool), np.full((1, HOURS), 200.0, np.float32),
                  np.full((1, HOURS), 200.0, np.float32))
    ob2 = observed_bounds(ObservationSet(2024, ObservationLog(), {"cms_generic": loud}, {}, {}), LIB, 1, g[:1], f_gas[:1], np.full(1, 1e7))
    assert ob2.states("mass", 50e3).tolist() == [1]                                        # measured emissions alone exceed the bar


def test_headline_metrics_present(res) -> None:
    m = res.scores.kpi_metrics["intensity"]
    for k in ("observed_certified_share_facilities", "observed_fails_share_facilities", "observed_share_of_year",
              "observed_certified_share_well_pads", "observed_certified_share_midstream", "certified_share_well_pads"):
        assert k in m
    assert 0.0 < m["observed_share_of_year"] < 0.35 and m["observed_certified_share_facilities"] == 0.0


def test_redundant_monitors_reach_full_year() -> None:
    """Redundant networks raise usable hours as 1 - (1 - a)^r; a high-availability network with two networks reaches whole years."""
    from mrvsim.observe.simulator import simulate_cms
    from mrvsim.population import generate_population
    pop = generate_population({"n_per_stratum": 30}, SeedTree(4))
    fac = np.arange(60)
    a = (1 - 0.03) * 0.85
    one = simulate_cms(LIB["cms_generic"], pop, fac, SeedTree(1), redundancy=1)
    two = simulate_cms(LIB["cms_generic"], pop, fac, SeedTree(1), redundancy=2)
    assert one.usable.mean() == pytest.approx(a, abs=0.01) and two.usable.mean() == pytest.approx(1 - (1 - a) ** 2, abs=0.01)
    np.testing.assert_array_equal(one.usable, simulate_cms(LIB["cms_generic"], pop, fac, SeedTree(1)).usable)   # redundancy 1 is the old series
    assert np.all(two.usable[one.usable])                                                                        # a second network only adds hours
    ha2 = simulate_cms(LIB["cms_high_availability"], pop, fac, SeedTree(1), redundancy=2)
    assert (ha2.usable.sum(axis=1) == HOURS).mean() > 0.5                                                         # most sites observed every hour
    cfg = RunConfig.from_dict({"seed": 5, "replications": 1, "population": {"n_per_stratum": 30},
                               "policy": {"sensors": {"cms_high_availability": {"coverage": 0.1, "targeting": "throughput", "redundancy": 2}}},
                               "estimator": {"n_draws": 300}})
    res = run_replication(cfg, SeedTree(cfg.seed), 0, LIB, n_draws=300, facilities_per_stratum=1)
    ob = res.observed; dep = res.plan.deployments["cms_high_availability"]
    assert dep.redundancy == 2 and res.scores.cost["cost_cms_high_availability_usd"] > 0
    full = ob.observed_hours[dep.facilities] >= HOURS
    assert full.mean() > 0.5
    st = ob.states("mass", 50e3)
    certified = dep.facilities[(st[dep.facilities] == 0)]
    assert certified.size > 0 and np.all(res.pop.true_mass_kg_yr()[certified] <= 50e3)                           # certified from observation, and rightly
    assert res.scores.kpi_metrics["mass"]["observed_certified_share_facilities"] > 0
