"""Quantification error (TDD section 4.2): ln r = ln q + beta_s + eps, eps ~ N(0, sigma_s^2)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm


@dataclass(frozen=True)
class QuantificationError:
    beta: float     # multiplicative bias in log space
    sigma: float    # log-scale spread

    def __post_init__(self) -> None:
        if self.sigma < 0:
            raise ValueError("sigma must be >= 0")

    def draw_reported(self, rng: np.random.Generator, q_true_kg_h: np.ndarray) -> np.ndarray:
        q = np.asarray(q_true_kg_h, dtype=float)
        return q * np.exp(self.beta + rng.normal(0.0, self.sigma, size=q.shape))

    def log_likelihood(self, r_kg_h: np.ndarray, q_kg_h: np.ndarray) -> np.ndarray:
        """log N(ln r | ln q + beta, sigma^2) (TDD section 6.3)."""
        r = np.asarray(r_kg_h, dtype=float)
        q = np.asarray(q_kg_h, dtype=float)
        with np.errstate(divide="ignore"):
            return norm.logpdf(np.log(r), loc=np.log(q) + self.beta, scale=max(self.sigma, 1e-9))

    def ratio_interval(self, level: float = 0.95) -> tuple[float, float]:
        """Central interval of r / q, e.g. (0.4, 1.9) means -60 % to +90 % at 95 %."""
        z = norm.ppf(0.5 + level / 2)
        return float(np.exp(self.beta - z * self.sigma)), float(np.exp(self.beta + z * self.sigma))

    @staticmethod
    def from_ratio_interval(lo: float, hi: float, level: float = 0.95) -> "QuantificationError":
        """(beta, sigma) that reproduce a published central interval of r / q."""
        z = norm.ppf(0.5 + level / 2)
        beta = 0.5 * (np.log(lo) + np.log(hi))
        sigma = (np.log(hi) - np.log(lo)) / (2 * z)
        return QuantificationError(float(beta), float(sigma))
