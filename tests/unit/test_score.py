"""Scoring tests (TDD section 7; CLAUDE.md Phase 5)."""

from __future__ import annotations

import numpy as np
import pytest

from mrvsim.estimate.fast import PosteriorSummary
from mrvsim.io.config import RunConfig
from mrvsim.io.seeds import SeedTree
from mrvsim.pipeline import run_replication, run_scored
from mrvsim.score import Bar, STATES, aggregate, classify, completeness_from_detection_probability, precise, prior_only, score_replication
from mrvsim.sensors import load_library

LIB = load_library()


def _post(n: int, med: np.ndarray, rel_w: float, kpi_i: np.ndarray | None = None) -> PosteriorSummary:
    lo5, hi95 = med * (1 - rel_w), med * (1 + rel_w)
    lo10, hi90 = med * (1 - 0.75 * rel_w), med * (1 + 0.75 * rel_w)
    im = med / 1e6 if kpi_i is None else kpi_i
    return PosteriorSummary(n, lo10, med, hi90, im * (1 - 0.75 * rel_w), im, im * (1 + 0.75 * rel_w), np.zeros(n), np.zeros(n), np.zeros(n), np.full(n, 1000.0),
                            mass_kg_yr_p05=lo5, mass_kg_yr_p95=hi95, intensity_p05=im * (1 - rel_w), intensity_p95=im * (1 + rel_w))


def test_classify_three_states_exhaustive() -> None:
    """Decision-only rule (DECISION_LOG 2026-10-01): certified iff p95 <= B, fails iff p05 > B, else straddles."""
    med = np.array([10e3, 10e3, 200e3, 55e3, 40e3, 60e3])          # kg/yr
    post = _post(6, med, rel_w=0.2)
    post.mass_kg_yr_p95[1] = 10e3 * 1.9; post.mass_kg_yr_p05[1] = 10e3 * 0.1    # wide but entirely below the bar
    bar = Bar("mass", 50e3, 0.30)
    st = classify(post, bar)
    assert STATES[st[0]] == "certified"        # p95 = 12 t <= 50 t
    assert STATES[st[1]] == "certified"        # p95 = 19 t <= 50 t: width no longer vetoes (w = 0.9 > 0.3)
    assert STATES[st[2]] == "fails"            # p05 = 160 t > 50 t
    assert STATES[st[3]] == "indeterminate"    # p05 = 44 t < 50 t < p95 = 66 t: straddles
    assert STATES[st[4]] == "certified"        # p95 = 48 t
    assert STATES[st[5]] == "indeterminate"    # p05 = 48 t < 50 t: not fails at 95 %, p95 = 72 t > 50 t: not certified
    assert set(np.unique(st)) <= {0, 1, 2}
    # precision is an attribute, reported separately
    pr = precise(post, bar)
    assert pr.tolist() == [True, False, True, True, True, True]
    # unscored facility
    post.mass_kg_yr_p50[0] = np.nan
    assert classify(post, bar)[0] == -1


def test_prior_only_flag_from_evidence_ratio_and_counts() -> None:
    med = np.full(4, 10e3)
    post = _post(4, med, rel_w=0.2)                       # posterior width 0.4 * med
    post.prior_mass_pcts = np.stack([med * 0.5, med, med * 1.5])          # log width ln 3 vs posterior ln 1.5 -> ratio 0.37
    post.prior_mass_pcts[:, 1] = [med[1] * 0.85, med[1], med[1] * 1.25]  # prior ln(1.47) < posterior ln(1.5) -> ratio 1.05
    post.evidence = np.array([[3, 3, 0, 3], [0, 0, 0, 0], [0, 0, 0, 500]])  # facility 2 saw nothing; 3 has CMS hours only
    bar = Bar("mass", 50e3, 0.30, prior_only_ratio=0.9)
    assert prior_only(post, bar).tolist() == [False, True, True, False]
    r = np.log(1.5) / np.log(3.0)
    assert np.allclose(post.evidence_ratio("mass"), [r, np.log(1.5) / np.log(1.25 / 0.85), r, r])
    # no prior summary (older runs) and no evidence: never flagged
    post.prior_mass_pcts = None; post.evidence = None
    assert not prior_only(post, bar).any()


def test_prob_below_from_quantile_grid() -> None:
    from mrvsim.estimate.fast import QGRID
    post = _post(2, np.array([10e3, 20e3]), rel_w=0.2)
    post.mass_quantiles = np.stack([np.linspace(5e3, 15e3, QGRID.size), np.linspace(10e3, 30e3, QGRID.size)], axis=1)
    pb = post.prob_below(14.5e3, "mass")
    assert abs(pb[0] - 0.95) < 1e-9 and abs(pb[1] - 0.225) < 1e-9
    assert post.prob_below(0.0, "mass").tolist() == [0.0, 0.0] and post.prob_below(1e9, "mass").tolist() == [1.0, 1.0]
    grid = np.array([post.prob_below(b, "mass")[0] for b in np.linspace(0, 20e3, 50)])
    assert np.all(np.diff(grid) >= 0)


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
    assert {"precise_share_facilities", "certified_precise_share_weighted_throughput", "certified_prior_only_share_facilities"} <= set(m)
    assert m["certified_precise_share_facilities"] <= m["certified_share_facilities"]
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
    # costs are on the basis of n_fac facilities in population proportions: n_fac x sum_i w_i cost_i (TDD section 3.1a)
    w = res.pop.stratum_weights("count")
    expand = lambda facilities: n_fac * w[facilities].sum()  # noqa: E731
    br, cms = res.plan.deployments["bridger_gml"].facilities, res.plan.deployments["cms_generic"].facilities
    assert cost["cost_bridger_gml_usd"] == pytest.approx(expand(br) * 2 * LIB["bridger_gml"].cost.per_site_visit_usd)
    assert cost["cost_cms_generic_usd"] == pytest.approx(expand(cms) * LIB["cms_generic"].cost.per_site_year_usd)
    log = res.obs.log
    gh = (log.sensor_idx == list(log.sensor_keys).index("ghgsat_c")) & ~log.incidental     # incidental looks are free
    assert cost["cost_ghgsat_c_usd"] == pytest.approx(expand(log.facility_idx[gh]) * LIB["ghgsat_c"].cost.per_tasking_usd)
    # targeted coverage refers to the population: the CMS facilities stand for at least 10 % of real facilities
    assert 0.10 <= w[cms].sum() < 0.10 + w[cms].max() + 1e-12
    assert cost["cost_total_usd"] == pytest.approx(cost["cost_bridger_gml_usd"] + cost["cost_cms_generic_usd"] + cost["cost_ghgsat_c_usd"])
    assert cost["cost_per_tonne_detected_usd"] > 0 and np.isfinite(cost["cost_per_tonne_detected_usd"])
    assert 0 <= res.scores.completeness <= 1
    assert res.scores.n_scored == 2 * len(res.pop.strata)
    assert n_fac == res.pop.strata.sizes(30).sum()


@pytest.mark.slow
def test_run_scored_persists_and_aggregates(tmp_path) -> None:
    cfg = RunConfig.from_dict({"name": "score-test", "seed": 5, "replications": 2, "population": {"n_per_stratum": 30},
                               "policy": {"sensors": {"bridger_gml": {"coverage": 1.0, "frequency_per_year": 2}}},
                               "estimator": {"n_draws": 400}, "scoring": {"bar_mass_t_yr": 50, "bar_intensity": 0.002, "w_max": 0.3}})
    report, run, last = run_scored(cfg, root=tmp_path, replications=2, n_draws=400, facilities_per_stratum=1)
    assert report.n_replications == 2
    assert report.kpi["mass"]["calibration"].n == 2 and np.isfinite(report.kpi["mass"]["calibration"].se)
    assert set(report.state_counts["mass"]) >= set(STATES) and "certified_prior_only" in report.state_counts["mass"]
    assert (run.dir / "summary.json").exists() and (run.dir / "population" / "sources.npz").exists() and (run.dir / "posterior_mass_pcts.npy").exists()
    for name in ("posterior_mass_quantiles", "posterior_intensity_quantiles", "prior_mass_pcts", "prior_intensity_pcts", "evidence_counts"):
        assert (run.dir / f"{name}.npy").exists(), name
    assert np.load(run.dir / "posterior_mass_quantiles.npy").shape[0] == 101 and np.load(run.dir / "evidence_counts.npy").shape[0] == 3
    import json
    s = json.loads((run.dir / "summary.json").read_text())
    assert "calibration_ok" in s and "citation_keys" in s["meta"] and "ghgrp" in s["meta"]["citation_keys"]
    # reproducible across two runs
    report2, run2, _ = run_scored(cfg, root=tmp_path, replications=2, n_draws=400, facilities_per_stratum=1)
    assert report2.kpi["mass"]["calibration"].mean == report.kpi["mass"]["calibration"].mean
    assert report2.cost["cost_total_usd"].mean == report.cost["cost_total_usd"].mean


def test_population_weighted_shares_and_decided_emissions() -> None:
    """Shares and medians use the count weights; decided share is the weighted true mass at decided facilities (PRD 5.5a)."""
    from mrvsim.score.metrics import weighted_median
    med = np.array([10e3, 200e3, 55e3, 20e3])                    # certified, fails, indeterminate, certified at a 50 t bar
    post = _post(4, med, rel_w=0.2)
    post.mass_kg_yr_p05[3], post.mass_kg_yr_p95[3] = 20e3 * 0.5, 20e3 * 1.5        # a wider interval, still certified
    truth = np.array([9e3, 180e3, 50e3, 21e3])
    wc = np.array([0.6, 0.05, 0.05, 0.3]); wt = np.array([0.1, 0.5, 0.3, 0.1])
    bars = {"mass": Bar("mass", 50e3, 0.30), "intensity": Bar("intensity", 0.002, 0.30)}
    sc = score_replication(post, truth, truth / 1e6, np.zeros(4, int), wc, wt, np.ones(4), bars, 1.0, {}, {}, 1)
    m = sc.kpi_metrics["mass"]
    assert m["certified_share_facilities"] == pytest.approx(0.9) and m["certified_share_sample"] == pytest.approx(0.5)
    assert m["fails_share_facilities"] == pytest.approx(0.05) and m["indeterminate_share_facilities"] == pytest.approx(0.05)
    assert m["certified_share_weighted_throughput"] == pytest.approx(0.2)
    decided = (0.6 * 9e3 + 0.05 * 180e3 + 0.3 * 21e3) / (0.6 * 9e3 + 0.05 * 180e3 + 0.05 * 50e3 + 0.3 * 21e3)
    assert m["decided_share_emitted_mass"] == pytest.approx(decided)
    assert m["width_median"] == pytest.approx(0.2) and m["width_median_weighted_throughput"] == pytest.approx(0.2)   # w = 0.2 holds 70 % of count weight
    assert weighted_median(np.array([1.0, 2.0, 3.0]), np.array([0.1, 0.1, 0.8])) == 3.0
    assert weighted_median(np.array([1.0, np.nan, 3.0]), np.array([0.6, 0.3, 0.1])) == 1.0
