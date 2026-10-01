"""Sequential importance sampling with resample-move (TDD section 6.7, fast path).

Plain importance sampling from the prior degenerates when the data are
informative (a few percent effective sample size on CMS-instrumented
facilities). This module tempers the likelihood, L^beta for 0 = beta_0 < ... <
beta_S = 1, choosing each step so that the conditional ESS stays near half the
particle count, and after every step resamples and rejuvenates each particle
with Metropolis moves that leave the tempered posterior invariant:

* a random-walk move on the continuous block (ln q_j, nu_on_j, nu_off_j of
  included sources) scaled by the weighted particle spread, and
* an independence move that redraws one candidate source (z_j, q_j, nu_j) or
  the source count K from the prior, so the discrete structure can change.

Because every proposal is symmetric or drawn from the prior, the acceptance
ratio is the tempered likelihood ratio alone. Each move costs one likelihood
evaluation of all particles, so the sampler is a small multiple of plain
importance sampling in cost and returns ESS close to the particle count.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from mrvsim.estimate.fast import PriorDraws, set_duration_moments
from mrvsim.estimate.likelihood import K_MAX
from mrvsim.population.priors import StratumPriors
from mrvsim.population.sources import draw_rates_kg_h, rate_params_for_type
from mrvsim.population.temporal import duty_cycle, lognormal_mean

LogLik = Callable[[PriorDraws], np.ndarray]


@dataclass
class SMCResult:
    draws: PriorDraws
    weights: np.ndarray
    loglik: np.ndarray
    ess: float
    n_stages: int
    acceptance: float


def _ess(logw: np.ndarray) -> float:
    w = np.exp(logw - logw.max()); w /= w.sum()
    return float(1.0 / np.sum(w**2))


def _next_beta(ll: np.ndarray, beta: float, target: float) -> float:
    """Largest beta' <= 1 such that ESS of the incremental weights (beta' - beta) * ll >= target (bisection)."""
    if _ess((1.0 - beta) * ll) >= target:
        return 1.0
    lo, hi = beta, 1.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if _ess((mid - beta) * ll) >= target:
            lo = mid
        else:
            hi = mid
    return lo


def _systematic_resample(rng: np.random.Generator, w: np.ndarray) -> np.ndarray:
    n = w.shape[0]
    u = (rng.random() + np.arange(n)) / n
    return np.searchsorted(np.cumsum(w), u).clip(0, n - 1)


def _copy(pd: PriorDraws, idx: np.ndarray, nu_on: np.ndarray, nu_off: np.ndarray) -> tuple[PriorDraws, np.ndarray, np.ndarray]:
    out = PriorDraws(pd.omega[idx].copy(), pd.z[idx].copy(), pd.q[idx].copy(), pd.pi[idx].copy(), pd.e_on[idx].copy(),
                     pd.e_off[idx].copy(), pd.var_on[idx].copy(), pd.var_off[idx].copy())
    return out, nu_on[idx].copy(), nu_off[idx].copy()


def _recompute_pi(pd: PriorDraws, nu_on: np.ndarray, nu_off: np.ndarray, sp: StratumPriors) -> None:
    n, k = pd.q.shape
    pi_int = duty_cycle(nu_on, np.full((n, k), sp.tau_on), nu_off, np.full((n, k), sp.tau_off))
    pd.pi = np.where(pd.z == 1, pi_int, 1.0)
    set_duration_moments(pd, nu_on, nu_off, sp)


def _draw_prior_with_nu(rng: np.random.Generator, sp: StratumPriors, n: int) -> tuple[PriorDraws, np.ndarray, np.ndarray]:
    """Prior draws plus the underlying nu arrays (needed for the continuous moves)."""
    K = np.minimum(rng.poisson(sp.lambda_k, size=n) + 1, K_MAX)
    omega = (np.arange(K_MAX)[None, :] < K[:, None]).astype(float)
    z = (rng.random((n, K_MAX)) < sp.p_intermittent).astype(np.int8)
    mu, sigma = rate_params_for_type(z, np.full((n, K_MAX), sp.mu_0), np.full((n, K_MAX), sp.sigma_0), np.full((n, K_MAX), sp.mu_1), np.full((n, K_MAX), sp.sigma_1))
    q = draw_rates_kg_h(rng, mu.ravel(), sigma.ravel(), np.full(n * K_MAX, sp.q_tail), np.full(n * K_MAX, sp.alpha)).reshape(n, K_MAX)
    nu_on = sp.nu_on + rng.normal(0.0, 1.0, size=(n, K_MAX)) * sp.sigma_nu_on
    nu_off = sp.nu_off + rng.normal(0.0, 1.0, size=(n, K_MAX)) * sp.sigma_nu_off
    pd = PriorDraws(omega, z, q, np.ones((n, K_MAX)), np.full((n, K_MAX), np.inf))
    _recompute_pi(pd, nu_on, nu_off, sp)
    return pd, nu_on, nu_off


def _log_prior_q(q: np.ndarray, z: np.ndarray, sp: StratumPriors) -> np.ndarray:
    """log prior density of ln q per source (lognormal body; Pareto tail above q_tail), summed over included sources later."""
    mu = np.where(z == 1, sp.mu_1, sp.mu_0); sig = np.where(z == 1, sp.sigma_1, sp.sigma_0)
    lnq = np.log(q)
    body = -0.5 * ((lnq - mu) / sig) ** 2 - np.log(sig)
    # tail: P(q > q_tail) from the lognormal, times Pareto density in ln q: alpha * exp(-alpha (lnq - ln q_tail))
    from scipy.stats import norm
    p_tail = norm.sf((np.log(sp.q_tail) - mu) / sig)
    tail = np.log(np.maximum(p_tail, 1e-300)) + np.log(sp.alpha) - sp.alpha * (lnq - np.log(sp.q_tail))
    return np.where(q > sp.q_tail, tail, body)


def smc_posterior(loglik: LogLik, sp: StratumPriors, rng: np.random.Generator, n_particles: int, ess_target: float = 0.5,
                  n_moves: int = 2, max_stages: int = 30) -> SMCResult:
    """Tempered SMC from the prior to the posterior. ``loglik(draws)`` returns log L per particle."""
    pd, nu_on, nu_off = _draw_prior_with_nu(rng, sp, n_particles)
    ll = loglik(pd)
    ll = np.where(np.isfinite(ll), ll, -np.inf)
    beta = 0.0
    logw = np.zeros(n_particles)
    n_acc = 0; n_prop = 0; stages = 0
    while beta < 1.0 and stages < max_stages:
        stages += 1
        new_beta = _next_beta(ll, beta, ess_target * n_particles)
        logw = (new_beta - beta) * ll
        beta = new_beta
        w = np.exp(logw - logw.max()); w /= w.sum()
        idx = _systematic_resample(rng, w)
        pd, nu_on, nu_off = _copy(pd, idx, nu_on, nu_off)
        ll = ll[idx]
        # --- rejuvenation moves targeting prior x L^beta ---
        for _ in range(n_moves):
            # (a) random-walk on continuous block of included sources; scale from the particle spread
            lnq = np.log(pd.q)
            s_q = np.maximum(np.std(lnq, axis=0), 0.05) * 0.5
            s_on = np.maximum(np.std(nu_on, axis=0), 0.05) * 0.5
            s_off = np.maximum(np.std(nu_off, axis=0), 0.05) * 0.5
            lnq_p = lnq + rng.normal(0, 1, lnq.shape) * s_q * pd.omega
            on_p = nu_on + rng.normal(0, 1, nu_on.shape) * s_on * pd.omega
            off_p = nu_off + rng.normal(0, 1, nu_off.shape) * s_off * pd.omega
            prop = PriorDraws(pd.omega, pd.z, np.exp(lnq_p), pd.pi.copy(), pd.e_on.copy())
            _recompute_pi(prop, on_p, off_p, sp)
            ll_p = loglik(prop); ll_p = np.where(np.isfinite(ll_p), ll_p, -np.inf)
            # prior ratio: q (lognormal/Pareto), nu (Normal); absent sources unchanged so cancel
            lp_cur = (_log_prior_q(pd.q, pd.z, sp) * pd.omega).sum(axis=1) - 0.5 * ((((nu_on - sp.nu_on) / max(sp.sigma_nu_on, 1e-6)) ** 2 + ((nu_off - sp.nu_off) / max(sp.sigma_nu_off, 1e-6)) ** 2) * pd.omega).sum(axis=1)
            lp_new = (_log_prior_q(prop.q, prop.z, sp) * pd.omega).sum(axis=1) - 0.5 * ((((on_p - sp.nu_on) / max(sp.sigma_nu_on, 1e-6)) ** 2 + ((off_p - sp.nu_off) / max(sp.sigma_nu_off, 1e-6)) ** 2) * pd.omega).sum(axis=1)
            log_alpha = beta * (ll_p - ll) + (lp_new - lp_cur)
            acc = np.log(rng.random(n_particles)) < log_alpha
            pd.q[acc] = prop.q[acc]; nu_on[acc] = on_p[acc]; nu_off[acc] = off_p[acc]
            for name in ("pi", "e_on", "e_off", "var_on", "var_off"):
                getattr(pd, name)[acc] = getattr(prop, name)[acc]
            ll = np.where(acc, ll_p, ll)
            n_acc += int(acc.sum()); n_prop += n_particles
            # (b) independence move: redraw one candidate source's (z, q, nu) from the prior, or K from its prior
            fresh, f_on, f_off = _draw_prior_with_nu(rng, sp, n_particles)
            j = rng.integers(0, K_MAX, size=n_particles)
            prop = PriorDraws(pd.omega.copy(), pd.z.copy(), pd.q.copy(), pd.pi.copy(), pd.e_on.copy())
            on_p, off_p = nu_on.copy(), nu_off.copy()
            rows = np.arange(n_particles)
            move_k = rng.random(n_particles) < 0.3
            # K move: take the fresh draw's omega (a prior draw of K), keeping this particle's sources
            prop.omega[move_k] = fresh.omega[move_k]
            # source move: replace source j
            sm = ~move_k
            for arr_p, arr_f in ((prop.z, fresh.z), (prop.q, fresh.q), (on_p, f_on), (off_p, f_off)):
                arr_p[rows[sm], j[sm]] = arr_f[rows[sm], j[sm]]
            _recompute_pi(prop, on_p, off_p, sp)
            ll_p = loglik(prop); ll_p = np.where(np.isfinite(ll_p), ll_p, -np.inf)
            log_alpha = beta * (ll_p - ll)          # prior proposal: prior ratio cancels
            acc = np.log(rng.random(n_particles)) < log_alpha
            for name in ("omega", "z", "q", "pi", "e_on", "e_off", "var_on", "var_off"):
                getattr(pd, name)[acc] = getattr(prop, name)[acc]
            nu_on[acc] = on_p[acc]; nu_off[acc] = off_p[acc]
            ll = np.where(acc, ll_p, ll)
            n_acc += int(acc.sum()); n_prop += n_particles
    w = np.full(n_particles, 1.0 / n_particles)
    return SMCResult(pd, w, ll, float(n_particles), stages, n_acc / max(n_prop, 1))
