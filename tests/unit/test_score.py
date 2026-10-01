"""Scoring tests (TDD section 7; CLAUDE.md Phase 5)."""

from __future__ import annotations

import numpy as np
import pytest

from mrvsim.estimate.fast import PosteriorSummary
from mrvsim.io.config import RunConfig
from mrvsim.io.seeds import SeedTree
from mrvsim.pipeline import run_replication, run_scored
from mrvsim.score import Bar, STATES, aggregate, classify, completeness_from_detection_probability, score_replication
from mrvsim.sensors import load_library

LIB = load_library()


def _post(n: int, med: np.ndarray, rel_w: float, kpi_i: np.ndarray | None = None) -> PosteriorSummary:
    lo5, hi95 = med * (1 - rel_w), med * (1 + rel_w)
    lo10, hi90 = med * (1 - 0.75 * rel_w), med * (1 + 0.75 * rel_w)
    im = med / 1e6 if kpi_i is None else kpi_i
    return PosteriorSummary(n, lo10, med, hi90, im * (1 - 0.75 * rel_w), im, im * (1 + 0.75 * rel_w), np.zeros(n), np.zeros(n), np.zeros(n), np.full(n, 1000.0),
                            mass_kg_yr_p05=lo5, mass_kg_yr_p95=hi95, intensity_p05=im * (1 - rel_w), intensity_p95=im * (1 + rel_w))


def test_classify_three_states_exhaustive() -> None:
    med = np.array([10e3, 10e3, 200e3, 55e3, 40e3])          # kg/yr
    post = _post(5, med, rel_w=0.2)
    post.mass_kg_yr_p95[1] = 10e3 * 1.9; post.mass_kg_yr_p05[1] = 10e3 * 0.1    # wide
    bar = Bar("mass", 50e3, 0.30)
    st = classify(post, bar)
    assert STATES[st[0]] == "certified"        # p90 = 11.5 t <= 50 t, w = 0.2
    assert STATES[st[1]] == "indeterminate"    # too wide
    assert STATES[st[2]] == "fails"            # p10 = 170 t > 50 t
    assert STATES[st[3]] == "indeterminate"    # p10 = 46.75 t < 50 t < p90 = 63 t: straddles
    assert STATES[st[4]] == "certified"        # p90 = 46 t
    assert set(np.unique(st)) <= {0, 1, 2}


def test_calibration_and_shares_with_weights() -> None:
    n = 400
    rng = np.random.default_rng(0)
    truth = np.exp(rng.normal(10, 1, n))
    med = truth * np.exp(rng.normal(0, 0.1, n))
    post = _post(n, med, rel_w=0.35)   # [0.65, 1.35] x med covers truth when |log err| < ~0.3 -> ~99 %
    stratum = np.repeat(np.arange(4), 100)
    wc = np.ones(n) / n
    wt = np.where(stratum == 0, 4.0, 1.0); wt /= wt.sum()
    bars = {"mass": Bar("mass", np.median(truth), 0.5), "intensity": Bar("intensity", 1.0, 0.5)}
    rs = score_replication(post, truth, med / 1e6, stratum, wc, wt, np.ones(n), bars, 0.5, {}, {"cost_total_usd": 1.0}, 4)
    m = rs.kpi_metrics["mass"]
    assert 0.95 <= m["calibration"] <= 1.0
    assert abs(m["width_median"] - 0.35) < 1e-9
    assert abs(m["bias_median"]) < 0.05
    assert abs(m["certified_share_facilities"] + m["fails_share_facilities"] + m["indeterminate_share_facilities"] - 1) < 1e-12
    assert 0 <= m["certified_share_weighted_throughput"] <= 1
    assert rs.per_stratum_calibration["mass"].shape == (4,)
    rep = aggregate([rs, rs], 4, [])
    assert rep.kpi["mass"]["calibration"].n == 2 and rep.kpi["mass"]["calibration"].se == 0.0


def test_completeness_bounds_and_definition() -> None:
    q = np.array([1.0, 20.0, 50.0, 5.0]); on = np.array([8760, 8760, 876, 8760])
    p = np.array([1.0, 0.5, 1.0, 1.0]); fac = np.array([0, 0, 1, 1]); basin = np.array([0, 1])
    c, by = completeness_from_detection_probability(q, on, p, fac, basin, ["a", "b"], 10.0)
    # only sources > 10 kg/h count: masses 20*8760 (P .5) and 50*876 (P 1)
    expected = (20 * 8760 * 0.5 + 50 * 876 * 1.0) / (20 * 8760 + 50 * 876)
    assert c == pytest.approx(expected)
    assert by["a"] == pytest.approx(0.5) and by["b"] == pytest.approx(1.0)
    assert 0 <= c <= 1


def test_cost_from_yaml_and_metrics() -> None:
    cfg = RunConfig.from_dict({"seed": 11, "replications": 1, "population": {"n_per_stratum": 30},
                               "policy": {"sensors": {"bridger_gml": {"coverage": 0.5, "frequency_per_year": 2},
                                                      "cms_generic": {"coverage": 0.1, "targeting": "throughput"},
                                                      "ghgsat_c": {"coverage": 0.1, "frequency_per_year": 6, "targeting": "throughput"}}},
                               "estimator": {"n_draws": 500}})
    res = run_replication(cfg, SeedTree(cfg.seed), 0, LIB, n_draws=500, facilities_per_stratum=2)
    cost = res.scores.cost
    n_fac = res.pop.n_facilities
    n_br = len(res.plan.deployments["bridger_gml"].facilities); n_cms = len(res.plan.deployments["cms_generic"].facilities)
    assert cost["cost_bridger_gml_usd"] == pytest.approx(n_br * 2 * LIB["bridger_gml"].cost.per_site_visit_usd)
    assert cost["cost_cms_generic_usd"] == pytest.approx(n_cms * LIB["cms_generic"].cost.per_site_year_usd)
    gh_rows = (res.obs.log.sensor_idx == list(res.obs.log.sensor_keys).index("ghgsat_c")).sum()
    assert cost["cost_ghgsat_c_usd"] == pytest.approx(gh_rows * LIB["ghgsat_c"].cost.per_tasking_usd)
    assert cost["cost_total_usd"] == pytest.approx(cost["cost_bridger_gml_usd"] + cost["cost_cms_generic_usd"] + cost["cost_ghgsat_c_usd"])
    assert cost["cost_per_tonne_detected_usd"] > 0 and np.isfinite(cost["cost_per_tonne_detected_usd"])
    assert 0 <= res.scores.completeness <= 1
    assert res.scores.n_scored == 2 * len(res.pop.strata)
    assert n_fac == 30 * len(res.pop.strata)


@pytest.mark.slow
def test_run_scored_persists_and_aggregates(tmp_path) -> None:
    cfg = RunConfig.from_dict({"name": "score-test", "seed": 5, "replications": 2, "population": {"n_per_stratum": 30},
                               "policy": {"sensors": {"bridger_gml": {"coverage": 1.0, "frequency_per_year": 2}}},
                               "estimator": {"n_draws": 400}, "scoring": {"bar_mass_t_yr": 50, "bar_intensity": 0.002, "w_max": 0.3}})
    report, run, last = run_scored(cfg, root=tmp_path, replications=2, n_draws=400, facilities_per_stratum=1)
    assert report.n_replications == 2
    assert report.kpi["mass"]["calibration"].n == 2 and np.isfinite(report.kpi["mass"]["calibration"].se)
    assert set(report.state_counts["mass"]) == set(STATES)
    assert (run.dir / "summary.json").exists() and (run.dir / "population" / "sources.npz").exists() and (run.dir / "posterior_mass_pcts.npy").exists()
    import json
    s = json.loads((run.dir / "summary.json").read_text())
    assert "calibration_ok" in s and "citation_keys" in s["meta"] and "ghgrp" in s["meta"]["citation_keys"]
    # reproducible across two runs
    report2, run2, _ = run_scored(cfg, root=tmp_path, replications=2, n_draws=400, facilities_per_stratum=1)
    assert report2.kpi["mass"]["calibration"].mean == report.kpi["mass"]["calibration"].mean
    assert report2.cost["cost_total_usd"].mean == report.cost["cost_total_usd"].mean
