"""Duty factor with imperfect detection (SPEC section 6a).

For an asset with clear looks j (detection flag d_j, detection probability POD_j(Q, u_j)), the likelihood of
the duty factor p (share of time a large source is on) is

    L(p, Q) = prod_j [p POD_j]^d_j [1 - p POD_j]^(1 - d_j).

The posterior of p is computed on a grid for each draw of Q and averaged over the Q draws. Ignoring POD would
treat missed plumes as "off" and bias p low. Priors: Jeffreys Beta(1/2, 1/2) by default [jeffreys]; the uniform
Beta(1, 1) is always reported alongside (SPEC 6a).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import beta as beta_dist

P_GRID = np.linspace(0.0, 1.0, 1001)
PRIORS = {"jeffreys": (0.5, 0.5), "uniform": (1.0, 1.0)}


@dataclass
class DutyPosterior:
    grid: np.ndarray            # p values
    density: np.ndarray         # posterior density on the grid (integrates to 1)
    prior: str
    n_looks: int
    n_detections: int

    def quantile(self, q: float | np.ndarray) -> np.ndarray:
        cdf = np.cumsum(self.density) * (self.grid[1] - self.grid[0])
        cdf = cdf / cdf[-1]
        return np.interp(q, cdf, self.grid)

    def sample(self, rng: np.random.Generator, n: int) -> np.ndarray:
        return self.quantile(rng.random(n))

    def mean(self) -> float:
        return float(np.trapezoid(self.grid * self.density, self.grid))


def log_prior(prior: str, grid: np.ndarray = P_GRID) -> np.ndarray:
    a, b = PRIORS[prior]
    with np.errstate(divide="ignore"):
        lp = beta_dist.logpdf(grid, a, b)
    return np.where(np.isfinite(lp), lp, -np.inf)


def duty_posterior(detected: np.ndarray, pod: np.ndarray, prior: str = "jeffreys", grid: np.ndarray = P_GRID) -> DutyPosterior:
    """Posterior of p given per-look detection flags and per-look POD (already evaluated at the Q draws).

    ``pod`` is (n_looks,) for one Q, or (n_draws, n_looks) to average the posterior over draws of Q.
    """
    d = np.asarray(detected, dtype=float).ravel()
    P = np.atleast_2d(np.asarray(pod, dtype=float))                  # (n_draws, n_looks)
    if P.shape[1] != d.shape[0]:
        raise ValueError("pod must have one column per look")
    pp = grid[None, None, :] * P[:, :, None]                          # p * POD_j, (n_draws, n_looks, n_grid)
    with np.errstate(divide="ignore", invalid="ignore"):
        hit = np.log(np.where(pp > 0, pp, 1.0)); hit = np.where(pp > 0, hit, -np.inf)          # log(p POD), -inf at p = 0
        miss = np.log1p(-np.minimum(pp, 1.0))                                                  # log(1 - p POD), -inf at p POD = 1
        ll = np.where(d[None, :, None] > 0, hit, miss).sum(axis=1)                            # (n_draws, n_grid)
    lp = ll + log_prior(prior, grid)[None, :]
    lp = lp - lp.max(axis=1, keepdims=True)
    dens = np.exp(lp)
    dens = dens / np.trapezoid(dens, grid, axis=1)[:, None]
    dens = np.nan_to_num(dens).mean(axis=0)
    dens = dens / np.trapezoid(dens, grid)
    return DutyPosterior(grid, dens, prior, int(d.size), int(d.sum()))


def large_source_ceiling(n_clear_looks: int, pod_mean: float, q: float = 0.95, prior: str = "jeffreys") -> float:
    """Upper quantile of p for an asset with no detections in n looks of mean POD (SPEC 6c: roughly 3/n for q = 0.95)."""
    post = duty_posterior(np.zeros(n_clear_looks), np.full(n_clear_looks, pod_mean), prior)
    return float(post.quantile(q))
