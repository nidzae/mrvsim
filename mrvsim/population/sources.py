"""Source count, type, and rate-when-on (TDD sections 3.2-3.3). Fully vectorised.

All functions take per-element parameter arrays (already indexed from stratum
priors) so that a whole population is drawn in a handful of numpy calls.
"""

from __future__ import annotations

import numpy as np


def draw_source_counts(rng: np.random.Generator, lambda_k: np.ndarray) -> np.ndarray:
    """K_i ~ Poisson(lambda_h) + 1 (TDD section 3.2) [sherwin2024; rutherford2021].

    The +1 guarantees at least one source per facility.
    """
    lam = np.asarray(lambda_k, dtype=float)
    return rng.poisson(lam).astype(np.int64) + 1


def draw_source_types(rng: np.random.Generator, p_intermittent: np.ndarray) -> np.ndarray:
    """z_ij ~ Bernoulli(p_h): 1 = intermittent, 0 = steady (TDD section 3.3)."""
    p = np.asarray(p_intermittent, dtype=float)
    return (rng.random(p.shape) < p).astype(np.int8)


def draw_rates_kg_h(
    rng: np.random.Generator,
    mu: np.ndarray,
    sigma: np.ndarray,
    q_tail: np.ndarray,
    alpha: np.ndarray,
) -> np.ndarray:
    """Lognormal body with a Pareto tail spliced above ``q_tail`` (TDD section 3.3).

    Implementation of the splice: draw ``q`` from the lognormal; wherever
    ``q > q_tail`` replace it with a Pareto draw ``q_tail * U**(-1/alpha)``.
    This leaves ``P(q > q_tail)`` equal to the lognormal tail mass and makes the
    conditional tail exactly ``P(q > x | q > q_tail) = (x / q_tail)**(-alpha)``,
    which is the TDD's statement of the splice. Rates below ``q_tail`` are
    unchanged. [cusworth2022; sherwin2024]
    """
    mu = np.asarray(mu, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    q_tail = np.asarray(q_tail, dtype=float)
    alpha = np.asarray(alpha, dtype=float)
    q = np.exp(rng.normal(mu, sigma))
    tail = q > q_tail
    if np.any(tail):
        u = rng.random(int(tail.sum()))
        # Deviation from a naive draw for numerical safety: guard U = 0 (probability ~1e-16).
        u = np.maximum(u, np.finfo(float).tiny)
        q[tail] = q_tail[tail] * u ** (-1.0 / alpha[tail])
    return q


def rate_params_for_type(
    z: np.ndarray,
    mu_0: np.ndarray, sigma_0: np.ndarray, mu_1: np.ndarray, sigma_1: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Select (mu, sigma) per source from its type (TDD section 3.3)."""
    z = np.asarray(z)
    mu = np.where(z == 1, mu_1, mu_0)
    sigma = np.where(z == 1, sigma_1, sigma_0)
    return mu, sigma
