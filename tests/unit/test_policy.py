"""Policy engine tests (TDD section 8; CLAUDE.md Phase 7)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_estimate import LIB, YEAR, _custom_population  # noqa: E402

from mrvsim.io.config import RunConfig
from mrvsim.io.seeds import SeedTree
from mrvsim.observe import build_plan
from mrvsim.pipeline import sensor_modes
from mrvsim.policy import (
    Policy, Rule, SearchSpace, SensorPolicy, TrialResult, allocate_budget_to_widest, evaluate_trigger_rules, optimize, pareto_front, simulate_with_rules,
)
from mrvsim.policy.policy import Policy as P


def test_policy_yaml_round_trip(tmp_path) -> None:
    pol = Policy(sensors={"bridger_gml": SensorPolicy(coverage=0.5, frequency_per_year=2, targeting="throughput"),
                          "ghgsat_c": SensorPolicy(coverage=0.3, frequency_per_year=12), "cms_generic": SensorPolicy(coverage=0.2)},
                 rules=[Rule("satellite_detect_to_aircraft", "ghgsat_c", "bridger_gml", X_kg_h=200.0, N_days=10),
                        Rule("cms_run_length_to_drone", "cms_generic", "bridger_gml", H_hours=48, N_days=7)], min_tier="A", name="demo")
    pol.save(tmp_path / "p.yaml")
    back = P.load(tmp_path / "p.yaml")
    assert back.to_dict() == pol.to_dict()
    cfg = back.to_policy_cfg()
    assert cfg["sensors"]["bridger_gml"]["coverage"] == 0.5 and len(cfg["rules"]) == 2
    assert P.from_policy_cfg(cfg).to_dict()["sensors"] == pol.to_dict()["sensors"]


def test_policy_validation_errors() -> None:
    with pytest.raises(ValueError, match="coverage"):
        Policy(sensors={"a": SensorPolicy(coverage=1.5)}).validate()
    with pytest.raises(ValueError, match="not a configured sensor"):
        Policy(sensors={"a": SensorPolicy()}, rules=[Rule("satellite_detect_to_aircraft", "a", "zzz")]).validate()
    with pytest.raises(ValueError, match="frequency_per_year"):
        Policy(sensors={"a": SensorPolicy(frequency_per_year=60)}).validate()


def test_satellite_detect_rule_cues_aircraft_within_n_days() -> None:
    pop = _custom_population(707, q_kg_h=3000.0, intermittent=False)   # big steady sources: GHGSat detects
    pol = Policy(sensors={"ghgsat_c": SensorPolicy(coverage=1.0, frequency_per_year=12), "bridger_gml": SensorPolicy(coverage=0.0, frequency_per_year=0)},
                 rules=[Rule("satellite_detect_to_aircraft", "ghgsat_c", "bridger_gml", X_kg_h=500.0, N_days=10, cooldown_days=30, max_visits_per_facility=3)])
    seeds = SeedTree(3)
    plan = build_plan(pol.to_policy_cfg(), seeds, pop.n_facilities, pop.lon, pop.throughput.gas_mkt_m3_yr, sensor_modes(LIB), YEAR)
    obs, cued = simulate_with_rules(pop, LIB, plan, pol, seeds, YEAR)
    cv = cued["bridger_gml"]
    assert len(cv) > 0
    # every cued visit is 1..N days after its trigger day and respects the per-facility cap
    days = np.asarray(cv.hour) // 24 - np.asarray(cv.trigger_day)
    assert np.all((days >= 1) & (days <= 10 + 1))
    assert max(np.bincount(np.asarray(cv.facility)).tolist()) <= 3
    # the full observation set now contains aircraft rows at exactly the cued (facility, hour) pairs
    log = obs.log; si = list(log.sensor_keys).index("bridger_gml"); m = log.sensor_idx == si
    assert m.sum() == len(cv)
    assert set(zip(log.facility_idx[m].tolist(), log.hour_idx[m].tolist())) == set(zip(cv.facility, cv.hour))
    # triggers in the two-pass simulation are the same observations as in the final set (named seed streams)
    gi = list(log.sensor_keys).index("ghgsat_c"); g = (log.sensor_idx == gi) & log.detected & (log.reported_kg_h >= 500)
    assert g.sum() >= len({(f, d) for f, d in zip(cv.facility, cv.trigger_day)})


def test_cms_run_length_rule() -> None:
    pop = _custom_population(808, q_kg_h=50.0, intermittent=True, nu_on=4.5, nu_off=5.0)   # long events (~100 h)
    pol = Policy(sensors={"cms_generic": SensorPolicy(coverage=1.0), "drone_generic": SensorPolicy(coverage=0.0, frequency_per_year=0)},
                 rules=[Rule("cms_run_length_to_drone", "cms_generic", "drone_generic", H_hours=24, N_days=5, cooldown_days=60)])
    seeds = SeedTree(4)
    plan = build_plan(pol.to_policy_cfg(), seeds, pop.n_facilities, pop.lon, pop.throughput.gas_mkt_m3_yr, sensor_modes(LIB), YEAR)
    obs, cued = simulate_with_rules(pop, LIB, plan, pol, seeds, YEAR)
    cv = cued["drone_generic"]
    assert len(cv) > 0
    assert "drone_generic" in obs.log.sensor_keys and (obs.log.source_idx >= 0).any()
    # cooldown respected
    for f in set(cv.facility):
        d = sorted(t for t, ff in zip(cv.trigger_day, cv.facility) if ff == f)
        assert all(b - a >= 60 for a, b in zip(d, d[1:]))


def test_budget_to_widest_interval_allocates_to_widest() -> None:
    pop = _custom_population(909, q_kg_h=20.0, intermittent=True)
    pol = Policy(sensors={"bridger_gml": SensorPolicy(coverage=0.5, frequency_per_year=1), "tropomi": SensorPolicy(coverage=1.0)},
                 rules=[Rule("budget_to_widest_interval", "", "bridger_gml", budget_per_month=2, max_visits_per_facility=2)])
    seeds = SeedTree(5)
    plan = build_plan(pol.to_policy_cfg(), seeds, pop.n_facilities, pop.lon, pop.throughput.gas_mkt_m3_yr, sensor_modes(LIB), YEAR)
    obs, cued = allocate_budget_to_widest(pol, pop, LIB, plan, seeds, YEAR, n_draws=400)
    assert len(cued) == 12 * 2
    assert max(np.bincount(np.asarray(cued.facility)).tolist()) <= 2
    si = list(obs.log.sensor_keys).index("bridger_gml")
    assert (obs.log.sensor_idx == si).sum() >= len(cued)


def test_pareto_front_logic() -> None:
    pol = Policy()
    ts = [TrialResult(0, pol, 100, 0.5, 0.9, 0.6, True), TrialResult(1, pol, 200, 0.4, 0.9, 0.6, True), TrialResult(2, pol, 150, 0.6, 0.9, 0.6, True),
          TrialResult(3, pol, 50, 0.3, 0.7, 0.6, False)]
    front = pareto_front(ts)
    assert [t.number for t in front] == [0, 1]          # 2 dominated by 0; 3 infeasible


@pytest.mark.slow
def test_optuna_study_returns_pareto_set(tmp_path) -> None:
    cfg = RunConfig.from_dict({"name": "opt", "seed": 2, "replications": 2, "population": {"n_per_stratum": 30}, "estimator": {"n_draws": 300},
                               "scoring": {"bar_mass_t_yr": 50, "bar_intensity": 0.01, "w_max": 1.5}})
    space = SearchSpace(sensors=("bridger_gml", "cms_generic"), frequency_range=(0, 4), fixed={"tropomi": SensorPolicy(coverage=1.0)})
    res = optimize(cfg, LIB, space, n_trials=4, kappa_min=0.0, w_max=5.0, theta=0.0, replications_trial=1, replications_full=2,
                   n_draws=300, facilities_per_stratum=1, top_k=2, root=tmp_path)
    assert len(res.trials) == 4
    assert all(t.feasible for t in res.pareto)
    assert len(res.rescored_top) == min(2, len(res.pareto))
    d = res.to_dict(); assert "pareto" in d and d["meta"]["sampler"] == "NSGAII"
