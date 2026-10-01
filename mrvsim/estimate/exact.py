"""Exact estimator: PyMC model for one facility (TDD section 6.7, exact path) [pymc].

Latents: K (Categorical over 1..K_max with the Poisson(lambda)+1 prior), z_j
(Bernoulli), ln q_j (Normal by type; the Pareto tail is approximated by the
lognormal body in the exact path and noted), nu_on_j / nu_off_j (Normal) giving
pi_j, and ln G. The snapshot likelihood enumerates all 2^K_max states with
absent sources forced off (pi_j -> 0 for j >= K), exactly as TDD section 6.3.
Discrete latents are sampled with Gibbs/Metropolis steps and continuous ones
with NUTS (PyMC compound step).

Used for validation runs and drill-down; ~5-20 s per facility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from mrvsim.estimate.inputs import EstimatorInputs
from mrvsim.estimate.likelihood import K_MAX, SensorParams
from mrvsim.population.priors import StratumPriors

HOURS_PER_YEAR = 8760


@dataclass
class ExactPosterior:
    mass_kg_yr: np.ndarray          # posterior draws
    intensity: np.ndarray
    pi_mean: np.ndarray
    percentiles: dict[str, tuple[float, float, float]]   # (p5, p50, p95)
    diagnostics: dict[str, Any]
    method: str = "exact-pymc"


def exact_posterior(i: int, inputs: EstimatorInputs, sp_h: StratumPriors, sparams: list[SensorParams], seed: int,
                    draws: int = 1000, tune: int = 1000, chains: int = 2) -> ExactPosterior:
    import pymc as pm
    import pytensor.tensor as pt

    sn, sv, cm = inputs.snapshots, inputs.surveys, inputs.cms
    rho = float(inputs.rho_surf[i])
    S = ((np.arange(2**K_MAX)[:, None] >> np.arange(K_MAX)[None, :]) & 1).astype(float)   # (256, 8)
    # K prior: Poisson(lambda)+1 truncated to K_max
    from scipy.stats import poisson
    pk = poisson.pmf(np.arange(K_MAX), sp_h.lambda_k); pk[-1] += 1 - pk.sum(); pk = pk / pk.sum()

    with pm.Model() as model:
        K = pm.Categorical("K", p=pk)                                    # K-1 in 0..7
        omega = pt.cast(pt.arange(K_MAX) <= K, "float64")
        z = pm.Bernoulli("z", p=sp_h.p_intermittent, shape=K_MAX)
        mu = pt.where(pt.eq(z, 1), sp_h.mu_1, sp_h.mu_0); sig = pt.where(pt.eq(z, 1), sp_h.sigma_1, sp_h.sigma_0)
        lnq = pm.Normal("lnq", mu=mu, sigma=sig, shape=K_MAX)
        q = pt.exp(lnq)
        nu_on = pm.Normal("nu_on", mu=sp_h.nu_on, sigma=max(sp_h.sigma_nu_on, 1e-3), shape=K_MAX)
        nu_off = pm.Normal("nu_off", mu=sp_h.nu_off, sigma=max(sp_h.sigma_nu_off, 1e-3), shape=K_MAX)
        e_on = pt.exp(nu_on + 0.5 * sp_h.tau_on**2); e_off = pt.exp(nu_off + 0.5 * sp_h.tau_off**2)
        pi_int = e_on / (e_on + e_off)
        pi = pt.where(pt.eq(z, 1), pi_int, 1.0)
        pi_eff = pt.clip(pi * omega, 1e-12, 1 - 1e-12) * omega           # absent -> 0
        lnG = pm.Normal("lnG", mu=np.log(float(inputs.g_hat_kg_yr[i])), sigma=max(float(inputs.sigma_g[i]), 1e-4))

        Q = pt.dot(S, q * omega)                                          # (256,)
        logw = pt.sum(S * pt.log(pt.clip(pi_eff, 1e-300, 1.0)) + (1 - S) * pt.log1p(-pi_eff), axis=1)   # absent: log(1)=0 for off

        def pod(sp: SensorParams, qe):
            return pt.where(qe > 0, pm.math.sigmoid(sp.a + sp.b * pt.log(pt.clip(qe, 1e-300, np.inf))), 0.0)

        total = 0.0
        for g in range(sn.nd_offset[i], sn.nd_offset[i + 1]):
            sp = sparams[sn.nd_sensor[g]]; f = float(sp.q_factor(float(sn.nd_wind[g]), rho))
            total = total + float(sn.nd_count[g]) * pm.math.logsumexp(logw + pt.log1p(-pod(sp, Q * f)))
        for d in range(sn.d_offset[i], sn.d_offset[i + 1]):
            sp = sparams[sn.d_sensor[d]]; f = float(sp.q_factor(float(sn.d_wind[d]), rho)); r = float(sn.d_rate[d])
            lnQ = pt.log(pt.clip(Q, 1e-300, np.inf))
            log_n = -0.5 * ((np.log(r) - lnQ - sp.beta) / sp.sigma) ** 2 - np.log(sp.sigma * np.sqrt(2 * np.pi))
            true_term = pm.math.logsumexp(logw + pt.log(pt.clip(pod(sp, Q * f), 1e-300, 1.0)) + log_n)
            fp = float(sp.log_fp_density(np.array([r]))[0])
            if sp.lam_fp > 0 and np.isfinite(fp):
                total = total + pm.math.logaddexp(np.log1p(-sp.lam_fp) + true_term, np.log(sp.lam_fp) + fp)
            else:
                total = total + true_term
        for v in range(sv.v_offset[i], sv.v_offset[i + 1]):
            sp = sparams[sv.v_sensor[v]]; f = float(sp.q_factor(float(sv.v_wind[v]), rho))
            dj = pt.clip(omega * pi * pod(sp, q * f), 0.0, 1 - 1e-9)
            total = total + pt.sum(pt.log1p(-dj))
            for r in sv.r_rate[sv.r_offset[v]:sv.r_offset[v + 1]]:
                log_n = -0.5 * ((np.log(float(r)) - lnq - sp.beta) / sp.sigma) ** 2 - np.log(sp.sigma * np.sqrt(2 * np.pi))
                odds = pt.log(pt.clip(dj / (1 - dj), 1e-300, np.inf)) + pt.where(omega > 0, 0.0, -np.inf)
                term = pm.math.logsumexp(odds + log_n)
                fp = float(sp.log_fp_density(np.array([float(r)]))[0])
                total = total + (pm.math.logaddexp(term, np.log(sp.lam_fp) + fp) if sp.lam_fp > 0 and np.isfinite(fp) else term)
        for c in np.nonzero(cm.facility == i)[0]:
            sp = sparams[cm.sensor[c]]
            if cm.n_usable[c] == 0:
                continue
            P = pod(sp, q) * omega
            dj = pt.clip(pi * P, 0.0, 1 - 1e-9)
            p_true = 1 - pt.prod(1 - dj)
            p = pt.clip(p_true + sp.lam_fp * (1 - p_true), 1e-6, 1 - 1e-6)
            n_eff = max(int(cm.n_runs_det[c] + cm.n_runs_nd[c]), 1)
            f_obs = float(cm.n_detected[c] / cm.n_usable[c])
            sd = pt.sqrt(p * (1 - p) / n_eff) + 2e-3
            total = total + (-0.5 * ((f_obs - p) / sd) ** 2 - pt.log(sd))
            u_frac = float(cm.n_usable[c]) / 8760.0
            sum_d = pt.sum(dj); w = dj / pt.maximum(sum_d, 1e-12)
            prod_all = pt.prod(1 - dj); p_other = 1 - prod_all / pt.maximum(1 - dj, 1e-12)
            inv_e_on = pt.where(pt.eq(z, 1), 1.0 / pt.maximum(e_on * max(u_frac, 1e-3), 1.0), 0.0) * omega
            hazard = pt.sum(w * (inv_e_on + (1 - P)) * (1 - p_other))
            m_true = 1.0 / pt.maximum(hazard, 1e-6)
            r_true = cm.n_usable[c] * p_true / pt.maximum(m_true, 1.0); r_fp = sp.lam_fp * cm.n_usable[c] * (1 - p_true)
            m_pred = (r_true * m_true + r_fp) / pt.maximum(r_true + r_fp, 1e-12)
            fp_share = sp.lam_fp * (1 - p_true) / pt.maximum(p_true + sp.lam_fp * (1 - p_true), 1e-12)
            if cm.n_runs_det[c] > 0 and np.isfinite(cm.mean_run_det[c]):
                sdm = np.sqrt(1.0 / cm.n_runs_det[c]) + 0.15
                total = total + (-0.5 * ((np.log(cm.mean_run_det[c]) - pt.log(pt.maximum(m_pred, 1.0))) / sdm) ** 2)
            if cm.n_detected[c] > 0 and np.isfinite(cm.mean_ln_rate[c]):
                s1 = pt.sum(dj * q); s2 = pt.sum(dj * q**2); s2dd = pt.sum(dj**2 * q**2); pp = pt.maximum(p_true, 1e-12)
                ey = s1 * np.exp(0.9**2 / 2) / pp
                ey2 = (s2 * np.exp(2 * 0.9**2) + pt.maximum(s1**2 - s2dd, 0.0) * np.exp(0.9**2)) / pp
                mu_true = pt.log(pt.maximum(ey, 1e-300)) - 0.5 * pt.log(pt.maximum(ey2, 1e-300) / pt.maximum(ey, 1e-300) ** 2) + sp.beta
                mu = (1 - fp_share) * mu_true + fp_share * sp.mean_log_fp_rate()
                total = total + (-0.5 * ((cm.mean_ln_rate[c] - mu) / 0.9) ** 2)
        pm.Potential("loglik", total)
        mass = pm.Deterministic("mass", pt.sum(omega * pi * q) * HOURS_PER_YEAR)
        pm.Deterministic("intensity", float(inputs.f_gas[i]) * mass / pt.exp(lnG))
        pm.Deterministic("pi_mean", pt.sum(omega * pi) / pt.maximum(pt.sum(omega), 1))
        idata = pm.sample(draws=draws, tune=tune, chains=chains, random_seed=seed, progressbar=False,
                          compute_convergence_checks=False, idata_kwargs={"log_likelihood": False})
    post = idata.posterior
    m = post["mass"].values.ravel(); it = post["intensity"].values.ravel(); pm_ = post["pi_mean"].values.ravel()
    pct = lambda x: tuple(float(v) for v in np.percentile(x, [5, 50, 95]))  # noqa: E731  two-sided 90 % interval
    import arviz as az
    rhat = float(az.rhat(idata, var_names=["mass"])["mass"].values) if chains > 1 else float("nan")
    return ExactPosterior(m, it, pm_, {"mass": pct(m), "intensity": pct(it), "pi_mean": pct(pm_)},
                          {"rhat_mass": rhat, "draws": int(m.size), "chains": chains})
