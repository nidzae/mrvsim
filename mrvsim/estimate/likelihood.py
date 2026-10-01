"""Likelihood terms (TDD sections 6.3-6.6), vectorised over prior draws.

All functions take draw arrays of shape ``(n_draws, K_max)`` for a *single*
facility's candidate sources — ``omega`` (inclusion), ``q`` (kg/h), ``pi`` — and
return one log-likelihood per draw. The fast estimator calls them per facility;
the exact estimator re-implements the same expressions in PyTensor.

Snapshot enumeration (TDD section 6.3)
--------------------------------------
For draws with K included sources the sum over on/off states runs over 2^K
states exactly; draws are grouped by K so that the common small-K draws do not
pay for K_max = 8. With ``pi_j`` = 0 for absent sources this equals the padded
2^K_max sum. When the largest possible facility rate of a draw gives a POD
below ``prune_tol`` for a sensor, the non-detection factor is 1 to within
``prune_tol`` and is skipped (bounded approximation, DECISION_LOG Phase 4).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.special import expit, logsumexp
from scipy.stats import norm

from mrvsim.sensors.schema import Sensor

K_MAX = 8
_STATES = {k: (np.arange(2**k)[:, None] >> np.arange(k)[None, :]) & 1 for k in range(1, K_MAX + 1)}   # (2^k, k)


@dataclass(frozen=True)
class SensorParams:
    """Plain-array view of the sensor fields the likelihood needs."""

    a: float
    b: float
    gamma: float
    u_ref: float
    u_min: float
    phi_enabled: bool
    rho_ref: float
    rho_p10: float
    phi_p10: float
    beta: float
    sigma: float
    lam_fp: float
    fp_q_max: float
    site_level: bool

    @classmethod
    def from_sensor(cls, s: Sensor) -> "SensorParams":
        sa = s.pod.surface
        return cls(s.pod.a, s.pod.b, s.pod.gamma, s.pod.u_ref_m_s, s.pod.u_min_m_s, sa.enabled, sa.rho_ref, sa.rho_p10, sa.phi_at_p10,
                   s.quantification.beta, s.quantification.sigma, s.false_positive.rate_per_opportunity,
                   s.false_positive.reported_rate_quantile_max, s.spatial_scope == "site")

    def q_factor(self, wind: np.ndarray | float, rho: np.ndarray | float) -> np.ndarray:
        """(u_ref / max(u, u_min))^gamma * phi(rho), broadcasting over wind and rho (TDD section 4.1)."""
        f = np.ones_like(np.asarray(wind, dtype=float))
        if self.gamma != 0.0:
            u = np.maximum(np.asarray(wind, dtype=float), self.u_min)
            f = f * (self.u_ref / u) ** self.gamma
        if self.phi_enabled:
            slope = (1.0 - self.phi_p10) / max(self.rho_ref - self.rho_p10, 1e-9)
            f = f * np.clip(1.0 - slope * (self.rho_ref - np.asarray(rho, dtype=float)), 0.0, 1.0)
        return f

    def pod(self, q_eff: np.ndarray, a: np.ndarray | float | None = None, b: np.ndarray | float | None = None) -> np.ndarray:
        aa = self.a if a is None else a
        bb = self.b if b is None else b
        with np.errstate(divide="ignore"):
            p = expit(aa + bb * np.log(q_eff))
        return np.where(q_eff > 0, p, 0.0)

    def mean_log_fp_rate(self) -> float:
        """E[ln r] of a false call: u ~ U(0.05, fp_q_max), ln r = (logit u - a) / b."""
        lo, hi = 0.05, max(self.fp_q_max, 0.06)
        u = np.linspace(lo, hi, 201)
        return float(np.mean((np.log(u / (1 - u)) - self.a) / self.b))

    def log_fp_density(self, r: np.ndarray) -> np.ndarray:
        """Density of a false call's reported rate: POD quantile u ~ U(0.05, fp_q_max) (TDD section 4.4 as implemented)."""
        r = np.asarray(r, dtype=float)
        u = self.pod(r)
        lo, hi = 0.05, max(self.fp_q_max, 0.06)
        dens = self.b * u * (1 - u) / (hi - lo) / r      # d u / d r = b u (1-u) / r
        return np.where((u >= lo) & (u <= hi), np.log(np.maximum(dens, 1e-300)), -np.inf)


@dataclass
class StateEnumeration:
    """Exact 2^K enumeration grouped by drawn source count K (TDD section 6.3).

    ``groups`` holds, per K present in the draws, the draw indices, the state
    rates Q_S (n_k, 2^K) and log state weights (n_k, 2^K). Absent sources never
    enter, so this equals the padded 2^K_max sum at a fraction of the cost:
    the expected number of states per draw is E[2^K] (~15 for lambda = 2)
    rather than 256.
    """

    n_draws: int
    groups: list[tuple[np.ndarray, np.ndarray, np.ndarray]]
    q_max: np.ndarray            # (n_draws,) largest possible facility rate per draw (for pruning)


def enumerate_states(q: np.ndarray, pi: np.ndarray, omega: np.ndarray) -> StateEnumeration:
    n = q.shape[0]
    K = omega.sum(axis=1).astype(np.int64)
    groups = []
    for k in range(1, K_MAX + 1):
        sel = np.nonzero(K == k)[0]
        if sel.size == 0:
            continue
        S = _STATES[k]
        qk = q[sel][:, :k]; pk = np.clip(pi[sel][:, :k], 1e-12, 1 - 1e-12)
        Q = qk @ S.T
        logw = np.log(pk) @ S.T + np.log1p(-pk) @ (1 - S).T
        groups.append((sel, Q, logw))
    return StateEnumeration(n, groups, (q * omega).sum(axis=1))


def snapshot_nd_loglik(en: StateEnumeration, sp: SensorParams, wind: float, rho: float, count: int,
                       prune_tol: float = 1e-5, a: np.ndarray | None = None, b: np.ndarray | None = None) -> np.ndarray:
    """count * log E_S[1 - P_s(Q_S, c)] per draw (TDD section 6.3, non-detection).

    Draws whose largest possible rate has POD below ``prune_tol`` contribute
    log(1 - p) with |p| < prune_tol and are skipped (error <= count * prune_tol).
    """
    f = float(sp.q_factor(wind, rho))
    out = np.zeros(en.n_draws)
    active_all = sp.pod(en.q_max * f, a, b) > prune_tol
    if not active_all.any():
        return out
    for sel, Q, logw in en.groups:
        act = active_all[sel]
        if not act.any():
            continue
        idx = sel[act]
        aa = None if a is None else a[idx][:, None]
        bb = None if b is None else b[idx][:, None]
        p = sp.pod(Q[act] * f, aa, bb)
        with np.errstate(divide="ignore"):
            out[idx] = count * logsumexp(logw[act] + np.log1p(-p), axis=1)
    return out


def snapshot_d_loglik(en: StateEnumeration, sp: SensorParams, wind: float, rho: float, r: float,
                      a: np.ndarray | None = None, b: np.ndarray | None = None, lam_fp: float | None = None) -> np.ndarray:
    """log[(1 - lam) E_S[P_s(Q_S) N(ln r | ln Q_S + beta, sigma^2)] + lam f_FP(r)] per draw (TDD section 6.3, detection)."""
    lam = sp.lam_fp if lam_fp is None else lam_fp
    f = float(sp.q_factor(wind, rho))
    lse = np.full(en.n_draws, -np.inf)
    sig = max(sp.sigma, 1e-6)
    lnr = np.log(r)
    for sel, Q, logw in en.groups:
        aa = None if a is None else a[sel][:, None]
        bb = None if b is None else b[sel][:, None]
        p = sp.pod(Q * f, aa, bb)
        with np.errstate(divide="ignore"):
            lnq = np.log(np.where(Q > 0, Q, 1.0))
            log_n = -0.5 * ((lnr - lnq - sp.beta) / sig) ** 2 - np.log(sig * np.sqrt(2 * np.pi))
            log_true = logw + np.log(np.where(Q > 0, p, 0.0)) + log_n
        lse[sel] = logsumexp(log_true, axis=1)
    log_fp = float(sp.log_fp_density(np.array([r]))[0])
    if lam <= 0 or not np.isfinite(log_fp):
        return lse
    return np.logaddexp(np.log1p(-lam) + lse, np.log(lam) + log_fp)


def survey_loglik(q: np.ndarray, pi: np.ndarray, omega: np.ndarray, sp: SensorParams, wind: float, rho: float,
                  rates: np.ndarray, lam_fp: float | None = None) -> np.ndarray:
    """Per-source survey (TDD section 6.5) with Poisson-process association (DECISION_LOG Phase 4).

    d_j = omega_j pi_j P(q_j): probability candidate j is on and detected.
    log L = sum_j log(1 - d_j) + sum_k log( sum_j d_j/(1-d_j) N(ln r_k | ln q_j + beta, sigma^2) + lam f_FP(r_k) ).
    """
    lam = sp.lam_fp if lam_fp is None else lam_fp
    f = float(sp.q_factor(wind, rho))
    d = np.where(omega > 0, pi * sp.pod(q * f), 0.0)
    d = np.clip(d, 0.0, 1 - 1e-9)
    ll = np.log1p(-d).sum(axis=1)
    if rates.size == 0:
        return ll
    with np.errstate(divide="ignore"):
        lnq = np.log(np.where(q > 0, q, 1.0))
        odds = np.log(np.where(d > 0, d / (1 - d), 1e-300))                          # (n, K)
        for r in rates:
            log_n = norm.logpdf(np.log(r), loc=lnq + sp.beta, scale=max(sp.sigma, 1e-6))
            term = logsumexp(odds + np.where(omega > 0, log_n, -np.inf), axis=1)
            fp = float(sp.log_fp_density(np.array([r]))[0])
            ll = ll + (term if lam <= 0 else np.logaddexp(term, np.log(lam) + fp))
    return ll


def cms_run_length_prediction(d: np.ndarray, P: np.ndarray, e_on: np.ndarray, omega: np.ndarray, usable_frac: float,
                              lam_fp: float, n_usable: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Predicted (mean detected-run length, number of detected runs, FP share of detected hours) per draw.

    The facility flag is an OR over sources. A detected run ends in the next usable hour only if the source
    responsible for the current detection stops being detected -- it switches off (hazard 1/(E[D_on] u), with u the
    usable fraction because non-usable hours are spliced out) or is missed (1 - P_j) -- AND no other source is
    detected in that hour (probability prod_{k != j} (1 - d_k)). Responsibility weights are w_j = d_j / sum d.
    False calls add lambda n_u (1 - p) singleton runs; the reported mean run mixes true and false runs.
    """
    d = np.clip(d, 0.0, 1 - 1e-9)
    sum_d = d.sum(axis=1)
    p_true = 1.0 - np.prod(1.0 - d, axis=1)
    w = d / np.maximum(sum_d, 1e-12)[:, None]
    # probability that some OTHER source is detected: 1 - prod_{k != j}(1 - d_k) = 1 - prod_all / (1 - d_j)
    prod_all = np.prod(1.0 - d, axis=1)[:, None]
    p_other = 1.0 - prod_all / np.maximum(1.0 - d, 1e-12)
    inv_e_on = np.where(np.isfinite(e_on), 1.0 / np.maximum(e_on * max(usable_frac, 1e-3), 1.0), 0.0) * omega
    hazard = (w * (inv_e_on + (1.0 - P)) * (1.0 - p_other)).sum(axis=1)
    m_true = 1.0 / np.maximum(hazard, 1e-6)
    r_true = n_usable * p_true / np.maximum(m_true, 1.0)
    r_fp = lam_fp * n_usable * (1.0 - p_true)
    m_pred = (r_true * m_true + r_fp) / np.maximum(r_true + r_fp, 1e-12)
    fp_share = lam_fp * (1.0 - p_true) / np.maximum(p_true + lam_fp * (1.0 - p_true), 1e-12)
    return m_pred, r_true + r_fp, fp_share


def cms_loglik(q: np.ndarray, pi: np.ndarray, omega: np.ndarray, e_on: np.ndarray, sp: SensorParams,
               n_usable: int, n_detected: int, n_runs_det: int, n_runs_nd: int, mean_run_det: float, mean_ln_rate: float,
               sigma_rate: float = 0.9, n_hours: int = 8760) -> np.ndarray:
    """Composite likelihood on CMS run-length summaries (TDD section 6.4, v1; DECISION_LOG Phase 4).

    Statistics of the usable-hour series and their predictions per draw:
      f      detected-hour fraction;   p = p_true + lam (1 - p_true),  p_true = 1 - prod_j (1 - omega_j pi_j P_j)
      m_det  mean detected-run length; OR-hazard prediction, see :func:`cms_run_length_prediction`
      ln r   mean log reported rate;   (1 - phi) [ln(sum_j d_j q_j / p_true) + beta + Jensen] + phi E[ln r_FP]
    Each enters as a Gaussian whose scale reflects the number of runs (autocorrelation-aware) plus a model-error floor.
    """
    if n_usable == 0:
        return np.zeros(q.shape[0])
    P = np.where(omega > 0, sp.pod(q), 0.0)                       # gamma = 0 for CMS: no wind term
    d = np.clip(np.where(omega > 0, pi * P, 0.0), 0.0, 1 - 1e-9)
    p_true = 1.0 - np.prod(1.0 - d, axis=1)
    p = np.clip(p_true + sp.lam_fp * (1.0 - p_true), 1e-6, 1 - 1e-6)
    f_obs = n_detected / n_usable
    n_eff = max(n_runs_det + n_runs_nd, 1)
    ll = norm.logpdf(f_obs, loc=p, scale=np.sqrt(p * (1 - p) / n_eff) + 2e-3)
    m_pred, _, fp_share = cms_run_length_prediction(d, P, e_on, omega, n_usable / n_hours, sp.lam_fp, n_usable)
    if n_runs_det > 0 and np.isfinite(mean_run_det):
        ll = ll + norm.logpdf(np.log(mean_run_det), loc=np.log(np.maximum(m_pred, 1.0)), scale=np.sqrt(1.0 / n_runs_det) + 0.15)
    if n_detected > 0 and np.isfinite(mean_ln_rate):
        # The logged rate in a detected hour is the SUM of detected sources' reported rates; its expected log is
        # ln(sum_j d_j q_j / p_true) + beta plus a Jensen term between 0 (one source) and sigma^2/2 (many sources),
        # mixed with the false-call rate distribution. Tier C sigma, no 1/sqrt(n) reduction (correlated errors).
        mu_true = expected_log_detected_sum(d, q, p_true, sp.beta, sigma_rate)
        mu = (1.0 - fp_share) * mu_true + fp_share * sp.mean_log_fp_rate()
        ll = ll + norm.logpdf(mean_ln_rate, loc=mu, scale=sigma_rate)
    return ll


def expected_log_detected_sum(d: np.ndarray, q: np.ndarray, p_true: np.ndarray, beta: float, sigma: float) -> np.ndarray:
    """E[ln Y | flagged], Y = sum_j det_j q_j e^(beta + eps_j), det_j ~ Bern(d_j), eps_j ~ N(0, sigma^2), by lognormal
    moment matching: ln E[Y] - 0.5 ln(E[Y^2] / E[Y]^2). Exact for a single source; tends to ln(sum q) + beta + sigma^2/2
    for many comparable sources; close to ln q_dominant + beta when one source dominates."""
    s1 = (d * q).sum(axis=1)
    s2 = (d * q**2).sum(axis=1)
    s2dd = (d**2 * q**2).sum(axis=1)
    p = np.maximum(p_true, 1e-12)
    ey = s1 * np.exp(sigma**2 / 2) / p
    ey2 = (s2 * np.exp(2 * sigma**2) + np.maximum(s1**2 - s2dd, 0.0) * np.exp(sigma**2)) / p
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.log(np.maximum(ey, 1e-300)) - 0.5 * np.log(np.maximum(ey2, 1e-300) / np.maximum(ey, 1e-300) ** 2)
    return np.where(s1 > 0, out + beta, np.log(1e-3) + beta)


def denominator_draws(rng: np.random.Generator, g_hat: float, sigma_g: float, n: int) -> np.ndarray:
    """Posterior draws of G given G_hat = G exp(eta), eta ~ N(0, sigma_g^2), flat prior on ln G (TDD section 6.6)."""
    return g_hat * np.exp(-rng.normal(0.0, sigma_g, size=n))
