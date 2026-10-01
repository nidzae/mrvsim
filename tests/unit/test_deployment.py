"""Deployment scheduling tests (TDD section 5.1 as amended; DECISION_LOG 2026-10-01 "Regional flight campaigns")."""

from __future__ import annotations

import numpy as np

from mrvsim.io.seeds import SeedTree
from mrvsim.observe.deployment import build_plan, campaign_hours, campaign_hours_regional
from mrvsim.policy import Policy, SensorPolicy

MODES = {"bridger_gml": ("campaign", "snapshot", False), "ogi": ("survey", "per_source", False)}


def _facilities(n: int = 120, n_basins: int = 4, seed: int = 1) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    basin = np.repeat(np.arange(n_basins), n // n_basins)
    lon = -110.0 + 5.0 * basin + rng.random(n)
    return lon, basin


def test_regional_campaign_shares_windows_within_basin() -> None:
    lon, basin = _facilities()
    facs = np.arange(lon.size)
    f, D = 2, 5
    hours, vf = campaign_hours_regional(np.random.default_rng(0), facs, lon, basin, f, D)
    assert hours.size == facs.size * f and np.all(np.bincount(vf, minlength=lon.size) == f)   # every facility exactly f visits
    day = hours // 24
    slice_days = 365.0 / f
    for b in np.unique(basin):
        for k in range(f):
            m = (basin[vf] == b) & (day >= k * slice_days - 1) & (day < (k + 1) * slice_days + 1)
            d = day[m]
            assert d.size == (basin == b).sum(), (b, k, d.size)
            assert d.max() - d.min() <= D, (b, k, d.min(), d.max())             # one window of at most D days
    # different basins are not forced onto the same days
    starts = [day[(basin[vf] == b) & (day < slice_days)].min() for b in np.unique(basin)]
    assert len(set(starts)) > 1
    assert np.all(hours >= 0) and np.all(hours < 8760)


def test_regional_campaign_window_never_longer_than_slice() -> None:
    lon, basin = _facilities(n=40, n_basins=2)
    hours, vf = campaign_hours_regional(np.random.default_rng(3), np.arange(40), lon, basin, 52, campaign_days=30)
    day = hours // 24
    for b in (0, 1):
        for k in range(52):
            d = day[(basin[vf] == b) & (day >= np.floor(k * 365 / 52)) & (day < np.floor((k + 1) * 365 / 52))]
            if d.size:
                assert d.max() - d.min() <= 7


def test_build_plan_campaign_vs_independent_and_fallback() -> None:
    lon, basin = _facilities()
    thr = np.random.default_rng(2).lognormal(10, 1, lon.size)
    pol = {"sensors": {"bridger_gml": {"coverage": 1.0, "frequency_per_year": 2, "scheduling": "campaign", "campaign_days": 3}}}
    plan = build_plan(pol, SeedTree(7), lon.size, lon, thr, MODES, 2024, basin_idx=basin)
    dep = plan.deployments["bridger_gml"]
    day = dep.visit_hours // 24
    for b in np.unique(basin):
        d = day[(basin[dep.visit_facility] == b) & (day < 182)]
        assert d.max() - d.min() <= 3
    # without basin_idx the campaign request falls back to independent dates (identical to scheduling: independent)
    plan_nb = build_plan(pol, SeedTree(7), lon.size, lon, thr, MODES, 2024)
    pol_ind = {"sensors": {"bridger_gml": {"coverage": 1.0, "frequency_per_year": 2}}}
    plan_ind = build_plan(pol_ind, SeedTree(7), lon.size, lon, thr, MODES, 2024, basin_idx=basin)
    np.testing.assert_array_equal(plan_nb.deployments["bridger_gml"].visit_hours, plan_ind.deployments["bridger_gml"].visit_hours)
    # independent mode is the pre-existing behaviour
    h_ref, f_ref = campaign_hours(SeedTree(7).rng("policy", "dates", sensor="bridger_gml"), np.arange(lon.size), lon, 2)
    np.testing.assert_array_equal(plan_ind.deployments["bridger_gml"].visit_hours, h_ref)
    np.testing.assert_array_equal(plan_ind.deployments["bridger_gml"].visit_facility, f_ref)
    # reproducible
    plan2 = build_plan(pol, SeedTree(7), lon.size, lon, thr, MODES, 2024, basin_idx=basin)
    np.testing.assert_array_equal(plan2.deployments["bridger_gml"].visit_hours, dep.visit_hours)


def test_policy_round_trips_scheduling_fields() -> None:
    pol = Policy(sensors={"bridger_gml": SensorPolicy(coverage=0.5, frequency_per_year=2, scheduling="campaign", campaign_days=4)})
    pol.validate()
    cfg = pol.to_policy_cfg()
    assert cfg["sensors"]["bridger_gml"]["scheduling"] == "campaign" and cfg["sensors"]["bridger_gml"]["campaign_days"] == 4
    back = Policy.from_policy_cfg(cfg)
    assert back.sensors["bridger_gml"].scheduling == "campaign" and back.sensors["bridger_gml"].campaign_days == 4
    assert Policy.from_yaml(pol.to_yaml()).sensors["bridger_gml"].campaign_days == 4
    bad = Policy(sensors={"bridger_gml": SensorPolicy(scheduling="weekly")})
    try:
        bad.validate()
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "scheduling" in str(e)
