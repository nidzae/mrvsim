"""Tests that prove the statistics (SPEC section 8d)."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.stats import beta as beta_dist

from mdd.model.dutyfactor import duty_posterior, large_source_ceiling
from mdd.model.rate import rate_from_basin, rate_from_detections
from mdd.model.rollup import asset_draws, roll_up
from mdd.sensors import load_sensors


def test_hand_checked_case_beta_2_12() -> None:
    """One detection in 12 perfect looks with the uniform prior is Beta(2, 12) (SPEC 8d)."""
    det = np.zeros(12); det[3] = 1
    post = duty_posterior(det, np.ones(12), prior="uniform")
    for q in (0.05, 0.5, 0.95):
        assert post.quantile(q) == pytest.approx(beta_dist.ppf(q, 2, 12), abs=2e-3)
    jeff = duty_posterior(det, np.ones(12), prior="jeffreys")
    assert jeff.quantile(0.5) == pytest.approx(beta_dist.ppf(0.5, 1.5, 11.5), abs=2e-3)


def test_imperfect_detection_raises_the_duty_factor() -> None:
    """With POD 0.5, a miss is as likely 'on and missed' as 'off': the posterior sits higher than with perfect looks."""
    det = np.zeros(20); det[[2, 9]] = 1
    perfect = duty_posterior(det, np.ones(20)).mean()
    half = duty_posterior(det, np.full(20, 0.5)).mean()
    assert half > perfect * 1.5


def test_rule_of_three_ceiling() -> None:
    """No detections in n good looks: the 95th percentile of p falls roughly as 3/n."""
    for n in (12, 30, 100):
        c = large_source_ceiling(n, pod_mean=1.0, prior="uniform")
        assert 0.7 * 3 / n < c < 1.3 * 3 / n
    assert large_source_ceiling(30, pod_mean=0.5) > large_source_ceiling(30, pod_mean=1.0)   # poorer looks, higher ceiling


def test_rate_models() -> None:
    lib = load_sensors(); s = lib["tan"]
    one = rate_from_detections(np.array([400.0]), np.array([200.0]), s.log_sigma)
    assert one.basis == "single" and one.median_kg_h() == pytest.approx(400.0)
    many = rate_from_detections(np.array([300.0, 500.0, 800.0]), None, s.log_sigma)
    assert many.basis == "fitted" and 300 < many.median_kg_h() < 800
    prior = rate_from_basin(np.array([200.0, 300.0, 450.0, 600.0, 900.0, 1200.0]), s.detection_range_kg_h())
    q = prior.sample(np.random.default_rng(0), 5000)
    assert prior.lo_kg_h <= q.min() and q.max() <= prior.hi_kg_h


def test_synthetic_truth_coverage() -> None:
    """Simulate assets with known p and Q, generate looks through the POD curve, check 90 % intervals cover ~90 % (SPEC 8d)."""
    lib = load_sensors(); s = lib["tan"]; rng = np.random.default_rng(42)
    covered = used = 0
    for _ in range(400):
        p_true = rng.uniform(0.05, 0.6); q_true = float(np.exp(rng.normal(np.log(300.0), 0.5)))
        n_looks = int(rng.integers(8, 40)); wind = rng.uniform(2, 8, n_looks)
        on = rng.random(n_looks) < p_true
        det = on & (rng.random(n_looks) < s.pod(q_true, wind))
        if det.sum() == 0:
            continue
        rates = q_true * np.exp(rng.normal(0, s.log_sigma, int(det.sum())))
        rate = rate_from_detections(rates, None, s.log_sigma)
        qd = rate.sample(rng, 200)
        post = duty_posterior(det, s.pod(qd[:, None], wind[None, :]))
        covered += post.quantile(0.05) <= p_true <= post.quantile(0.95); used += 1
    assert used > 250
    assert 0.84 <= covered / used <= 0.97, covered / used


def test_roll_up_and_grades_shape() -> None:
    rng = np.random.default_rng(1); lib = load_sensors(); s = lib["tan"]
    det = np.zeros(20); det[5] = 1
    rate = rate_from_detections(np.array([500.0]), np.array([150.0]), s.log_sigma)
    duty = duty_posterior(det, s.pod(rate.sample(rng, 100)[:, None], np.full((1, 20), 3.0)))
    zero = duty_posterior(np.zeros(20), np.full((1, 20), 0.9))
    prior = rate_from_basin(np.array([200.0, 400.0, 800.0, 1600.0, 300.0]), s.detection_range_kg_h())
    a = asset_draws(rng, "A", "production", 1.0, 5.0e6, duty, rate, None, None, 0.0, "none", 0.9, 2000)
    b = asset_draws(rng, "B", "gathering", 0.5, 2.0e7, None, None, zero, prior, 1000.0, "literature_prior", 1.0, 2000)
    res = roll_up([a, b], 4.9e6)
    pct = res.percentiles(res.delivered_intensity)
    assert pct["p5"] <= pct["p50"] <= pct["p95"] and 0 <= res.evidence_share() <= 1
    assert set(res.segment_intensity) == {"production", "gathering"}
    assert res.contributions()[0]["share_of_assured"] >= res.contributions()[1]["share_of_assured"]
