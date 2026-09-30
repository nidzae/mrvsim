"""Probability-of-detection curves (TDD section 4.1).

P_s(q, c) = logistic(a_s + b_s ln q_eff),  q_eff = q (u_ref/u)^gamma_s phi_s(rho_surf).

All functions are vectorised and broadcast over their arguments.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.special import expit

_LN9 = float(np.log(9.0))  # logit(0.9) - logit(0.5)


@dataclass(frozen=True)
class SurfaceAdjustment:
    """phi_s(rho_surf): 1 at the test-site reflectance, linear decline to ``phi_at_p10``
    at the 10th percentile of US O&G-region reflectance (TDD section 4.1). ASSUMPTION, not measured.

    ``uncertainty_halfwidth`` is the uniform +/- relative uncertainty propagated by the
    estimator (TDD section 4.1: +/-50 %).
    """

    enabled: bool
    rho_ref: float           # reflectance of the blind-test site
    rho_p10: float           # 10th percentile of US O&G-region SWIR reflectance
    phi_at_p10: float = 0.5
    uncertainty_halfwidth: float = 0.5

    def phi(self, rho_surf: np.ndarray | float) -> np.ndarray:
        if not self.enabled:
            return np.ones_like(np.asarray(rho_surf, dtype=float))
        rho = np.asarray(rho_surf, dtype=float)
        slope = (1.0 - self.phi_at_p10) / max(self.rho_ref - self.rho_p10, 1e-9)
        phi = 1.0 - slope * (self.rho_ref - rho)
        # Deviation from the written linear form for physical validity: never brighten beyond
        # the test-site value and never fall below zero.
        return np.clip(phi, 0.0, 1.0)


@dataclass(frozen=True)
class PODCurve:
    """Logistic POD in ln q_eff with wind exponent and optional surface adjustment."""

    a: float
    b: float
    gamma: float
    u_ref_m_s: float
    surface: SurfaceAdjustment
    ab_cov: tuple[tuple[float, float], tuple[float, float]] | None = None  # Laplace posterior of (a, b)

    def __post_init__(self) -> None:
        if self.b <= 0:
            raise ValueError("POD slope b must be > 0 so that POD is increasing in q")

    def q_eff(self, q_kg_h: np.ndarray | float, wind_m_s: np.ndarray | float | None = None,
              rho_surf: np.ndarray | float | None = None) -> np.ndarray:
        q = np.asarray(q_kg_h, dtype=float)
        f = np.ones_like(q)
        if wind_m_s is not None and self.gamma != 0.0:
            u = np.maximum(np.asarray(wind_m_s, dtype=float), 0.1)  # guard u -> 0 (TDD section 4.1)
            f = f * (self.u_ref_m_s / u) ** self.gamma
        if rho_surf is not None:
            f = f * self.surface.phi(rho_surf)
        return q * f

    def prob(self, q_kg_h: np.ndarray | float, wind_m_s: np.ndarray | float | None = None,
             rho_surf: np.ndarray | float | None = None, a: np.ndarray | float | None = None,
             b: np.ndarray | float | None = None) -> np.ndarray:
        """P(detect). ``a``/``b`` override the point values (for propagating POD-parameter uncertainty)."""
        qe = self.q_eff(q_kg_h, wind_m_s, rho_surf)
        aa = self.a if a is None else a
        bb = self.b if b is None else b
        with np.errstate(divide="ignore"):
            lnq = np.log(qe)
        p = expit(aa + bb * lnq)
        return np.where(qe > 0, p, 0.0)

    def pod_quantile(self, p: float, wind_m_s: float | None = None) -> float:
        """Rate (kg/h) at which POD = p, at u_ref (or at the given wind) and phi = 1."""
        lnq_eff = (np.log(p / (1 - p)) - self.a) / self.b
        q = float(np.exp(lnq_eff))
        if wind_m_s is not None and self.gamma != 0.0:
            q = q / (self.u_ref_m_s / max(wind_m_s, 0.1)) ** self.gamma
        return q

    @property
    def pod50_kg_h(self) -> float:
        return self.pod_quantile(0.5)

    @property
    def pod90_kg_h(self) -> float:
        return self.pod_quantile(0.9)

    def sample_ab(self, rng: np.random.Generator, n: int) -> tuple[np.ndarray, np.ndarray]:
        """Draw (a, b) from the Laplace posterior; degenerate at the point value if no covariance."""
        if self.ab_cov is None:
            return np.full(n, self.a), np.full(n, self.b)
        draws = rng.multivariate_normal([self.a, self.b], np.asarray(self.ab_cov), size=n)
        return draws[:, 0], np.maximum(draws[:, 1], 1e-6)

    @staticmethod
    def ab_from_pod50_pod90(pod50_kg_h: float, pod90_kg_h: float) -> tuple[float, float]:
        """(a, b) such that POD(pod50) = 0.5 and POD(pod90) = 0.9 at u_ref, phi = 1."""
        if not pod90_kg_h > pod50_kg_h > 0:
            raise ValueError("need 0 < pod50 < pod90")
        b = _LN9 / (np.log(pod90_kg_h) - np.log(pod50_kg_h))
        a = -b * np.log(pod50_kg_h)
        return float(a), float(b)
