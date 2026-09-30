"""Distribution-shape tests for TDD sections 3.2-3.3."""

from __future__ import annotations

import numpy as np
from scipy import stats

from mrvsim.population.sources import draw_rates_kg_h, draw_source_counts, draw_source_types


def test_source_count_at_least_one_and_poisson_mean() -> None:
    rng = np.random.default_rng(0)
    lam = np.full(200_000, 2.0)
    k = draw_source_counts(rng, lam)
    assert k.min() >= 1
    assert abs(k.mean() - 3.0) < 0.02          # E[K] = lambda + 1
    assert abs(k.var() - 2.0) < 0.05           # Var[K] = lambda


def test_source_type_frequency() -> None:
    rng = np.random.default_rng(1)
    z = draw_source_types(rng, np.full(100_000, 0.4))
    assert set(np.unique(z)) <= {0, 1}
    assert abs(z.mean() - 0.4) < 0.01


def test_rates_lognormal_body_below_splice() -> None:
    rng = np.random.default_rng(2)
    n = 200_000
    mu, sigma, q_tail, alpha = 0.0, 1.0, 1e9, 1.5   # splice never triggers
    q = draw_rates_kg_h(rng, np.full(n, mu), np.full(n, sigma), np.full(n, q_tail), np.full(n, alpha))
    ks = stats.kstest(np.log(q), "norm", args=(mu, sigma))
    assert ks.pvalue > 0.01


def test_rates_pareto_tail_above_splice() -> None:
    rng = np.random.default_rng(3)
    n = 400_000
    mu, sigma, q_tail, alpha = 2.0, 1.5, 50.0, 1.5
    q = draw_rates_kg_h(rng, np.full(n, mu), np.full(n, sigma), np.full(n, q_tail), np.full(n, alpha))
    # Tail mass is preserved from the lognormal.
    p_tail_expected = 1 - stats.norm.cdf((np.log(q_tail) - mu) / sigma)
    assert abs((q > q_tail).mean() - p_tail_expected) < 0.005
    # Conditional tail is Pareto(alpha): Hill estimator of alpha.
    tail = q[q > q_tail]
    alpha_hat = 1.0 / np.mean(np.log(tail / q_tail))
    assert abs(alpha_hat - alpha) < 0.05
    # Body below the splice is untouched.
    body = q[q <= q_tail]
    trunc = stats.truncnorm((-np.inf), (np.log(q_tail) - mu) / sigma, loc=mu, scale=sigma)
    assert stats.kstest(np.log(body), trunc.cdf).pvalue > 0.01


def test_rates_positive_and_splice_thickens_tail() -> None:
    """The Pareto splice raises the mass share of the top 1% relative to the plain lognormal."""
    n = 200_000
    args = (np.zeros(n), np.full(n, 1.5))
    q_plain = draw_rates_kg_h(np.random.default_rng(4), *args, np.full(n, 1e12), np.full(n, 1.2))
    q_splice = draw_rates_kg_h(np.random.default_rng(4), *args, np.full(n, 100.0), np.full(n, 1.2))
    assert np.all(q_splice > 0)
    share = lambda q: np.sort(q)[-n // 100:].sum() / q.sum()  # noqa: E731
    assert share(q_splice) > share(q_plain)
    assert share(q_splice) > 0.15   # heavy-tailed: top 1% of sources carry a large share of mass
