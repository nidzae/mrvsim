"""POD fitting utility (CLAUDE.md Phase 2; TDD section 4.1).

Given a blind controlled-release table with columns ``rate_kg_h``, ``detected``
(0/1), optional ``wind_m_s`` and ``rho_surf``, fit the logistic POD parameters
(a, b) by maximum likelihood on ln q_eff and return the Laplace approximation
to their posterior (mean = MLE, covariance = inverse observed information).

A weak Gaussian ridge N(0, 10^2) on (a, b) is included so that tables with
complete separation (every large release detected, every small one missed)
still give a finite estimate; its effect is negligible for informative tables
and it is reported in the fit record.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit

from mrvsim.sensors.pod import PODCurve, SurfaceAdjustment

_RIDGE_SD = 10.0


@dataclass(frozen=True)
class PODFit:
    a: float
    b: float
    cov: tuple[tuple[float, float], tuple[float, float]]
    n_releases: int
    n_detected: int
    log_likelihood: float
    gamma: float
    u_ref_m_s: float
    converged: bool
    ridge_sd: float = _RIDGE_SD

    @property
    def se_a(self) -> float:
        return float(np.sqrt(self.cov[0][0]))

    @property
    def se_b(self) -> float:
        return float(np.sqrt(self.cov[1][1]))

    def to_curve(self, surface: SurfaceAdjustment) -> PODCurve:
        return PODCurve(a=self.a, b=self.b, gamma=self.gamma, u_ref_m_s=self.u_ref_m_s, surface=surface, ab_cov=self.cov)

    def record(self) -> dict[str, Any]:
        return asdict(self)


def fit_pod_logistic(
    table: pd.DataFrame | Mapping[str, Any],
    gamma: float = 1.0,
    u_ref_m_s: float = 3.0,
    surface: SurfaceAdjustment | None = None,
) -> PODFit:
    """Maximum-likelihood logistic POD fit with Laplace posterior.

    Parameters
    ----------
    table:
        Columns ``rate_kg_h`` (> 0; zero-rate rows are false-positive tests and are
        dropped), ``detected`` (bool/0-1), optional ``wind_m_s``, ``rho_surf``.
    gamma, u_ref_m_s:
        Wind normalisation applied before fitting (TDD section 4.1); the fitted
        (a, b) are therefore in terms of q_eff.
    surface:
        Optional surface adjustment applied before fitting when ``rho_surf`` is present.
    """
    df = pd.DataFrame(table)
    if "rate_kg_h" not in df or "detected" not in df:
        raise ValueError("table needs columns 'rate_kg_h' and 'detected'")
    df = df[df["rate_kg_h"] > 0].copy()
    if len(df) < 3:
        raise ValueError("need at least 3 non-zero releases to fit a POD curve")
    y = df["detected"].astype(float).to_numpy()
    q = df["rate_kg_h"].to_numpy(dtype=float)
    f = np.ones_like(q)
    if "wind_m_s" in df and gamma != 0.0:
        u = np.maximum(df["wind_m_s"].to_numpy(dtype=float), 0.1)
        f = f * (u_ref_m_s / u) ** gamma
    if surface is not None and "rho_surf" in df:
        f = f * surface.phi(df["rho_surf"].to_numpy(dtype=float))
    x = np.log(q * f)

    def negloglik(theta: np.ndarray) -> float:
        a, b = theta
        eta = a + b * x
        # log-likelihood of Bernoulli with logit eta, numerically stable
        ll = np.sum(y * eta - np.logaddexp(0.0, eta))
        ll -= 0.5 * (a**2 + b**2) / _RIDGE_SD**2
        return -ll

    def grad(theta: np.ndarray) -> np.ndarray:
        a, b = theta
        p = expit(a + b * x)
        r = y - p
        return -np.array([r.sum() - a / _RIDGE_SD**2, (r * x).sum() - b / _RIDGE_SD**2])

    def hess(theta: np.ndarray) -> np.ndarray:
        a, b = theta
        p = expit(a + b * x)
        w = p * (1 - p)
        h = np.array([[w.sum(), (w * x).sum()], [(w * x).sum(), (w * x * x).sum()]])
        return h + np.eye(2) / _RIDGE_SD**2

    # Start from a crude POD50 guess: median ln q_eff of the mixed region.
    x0 = np.array([-np.median(x), 1.0])
    res = minimize(negloglik, x0, jac=grad, hess=hess, method="trust-exact")
    a_hat, b_hat = res.x
    if b_hat <= 0:
        # A decreasing POD is not admissible (TDD section 4.1); re-fit with b constrained.
        res = minimize(negloglik, x0, jac=grad, method="L-BFGS-B", bounds=[(None, None), (1e-6, None)])
        a_hat, b_hat = res.x
    cov = np.linalg.inv(hess(res.x))
    return PODFit(
        a=float(a_hat), b=float(b_hat), cov=tuple(map(tuple, cov.tolist())),  # type: ignore[arg-type]
        n_releases=int(len(df)), n_detected=int(y.sum()), log_likelihood=float(-res.fun),
        gamma=gamma, u_ref_m_s=u_ref_m_s, converged=bool(res.success),
    )
