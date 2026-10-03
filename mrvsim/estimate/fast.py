"""Fast estimator: importance sampling from the stratum prior (TDD section 6.7).

For each facility, ``n_draws`` prior samples of {K, z_j, q_j, pi_j} (TDD section
6.1-6.2) are weighted by the exact likelihood of the facility's observation log
(section 6.3-6.6) and the weighted 10/50/90 percentiles of M_hat = sum_j omega_j
pi_j q_j T and I_hat = f_gas M_hat / G are reported. The effective sample size
of the weights is returned as a diagnostic; facilities with ESS below
``ess_warn`` are flagged (their interval is unreliable and the exact path
should be used).

The prior draws reuse the population generator's sampling functions so that the
estimator's prior is, by construction, the population's generating distribution
(empirical Bayes, TDD section 6.2). Every draw comes from ``seeds`` so results
are reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from mrvsim.estimate.inputs import EstimatorInputs, evidence_counts
from mrvsim.estimate.likelihood import (
    K_MAX, SensorParams, cms_loglik, denominator_draws, enumerate_states, snapshot_d_loglik, snapshot_nd_loglik, survey_loglik,
)
from mrvsim.io.seeds import SeedTree
from mrvsim.population.priors import PriorSet, StratumPriors
from mrvsim.population.sources import draw_rates_kg_h, rate_params_for_type
from mrvsim.population.strata import StrataTable
from mrvsim.population.temporal import duty_cycle, lognormal_mean
from mrvsim.sensors.library import SensorLibrary

HOURS_PER_YEAR = 8760
QGRID = np.arange(0, 101, dtype=float)   # posterior quantile grid in percent (DECISION_LOG 2026-10-01)


def log_width_ratio(lo: np.ndarray, hi: np.ndarray, prior_lo: np.ndarray, prior_hi: np.ndarray) -> np.ndarray:
    """ln(hi/lo) / ln(prior_hi/prior_lo), nan where any bound is non-positive or the prior has zero log width."""
    lo, hi, plo, phi = (np.asarray(a, dtype=float) for a in (lo, hi, prior_lo, prior_hi))
    ok = (lo > 0) & (hi > 0) & (plo > 0) & (phi > plo)
    out = np.full(lo.shape, np.nan)
    out[ok] = np.log(hi[ok] / lo[ok]) / np.log(phi[ok] / plo[ok])
    return out


@dataclass
class PosteriorSummary:
    """Per-facility posterior percentiles (TDD section 6.7) plus diagnostics."""

    n_fac: int
    mass_kg_yr_p10: np.ndarray
    mass_kg_yr_p50: np.ndarray
    mass_kg_yr_p90: np.ndarray         # one-sided 90 % upper credible bound K_U,90 (PRD section 5.3, certification)
    intensity_p10: np.ndarray
    intensity_p50: np.ndarray
    intensity_p90: np.ndarray
    pi_mean_p10: np.ndarray            # posterior on the facility's mean duty cycle of included sources
    pi_mean_p50: np.ndarray
    pi_mean_p90: np.ndarray
    ess: np.ndarray
    method: str = "fast"
    n_draws: int = 0
    meta: dict[str, Any] = field(default_factory=dict)
    # two-sided 90 % credible interval [p5, p95] used for calibration and width (DECISION_LOG 2026-09-30, Phase 4)
    mass_kg_yr_p05: np.ndarray | None = None
    mass_kg_yr_p95: np.ndarray | None = None
    intensity_p05: np.ndarray | None = None
    intensity_p95: np.ndarray | None = None
    # Posterior quantile grid (len(QGRID), n_fac) so P(K <= B | data) can be read at any bar (DECISION_LOG 2026-10-01
    # "certification is the compliance decision at 95 %"); the five percentiles above are rows of it.
    mass_quantiles: np.ndarray | None = None
    intensity_quantiles: np.ndarray | None = None
    # Prior (no-observation) p05/p50/p95 on common random numbers, for the evidence ratio (PRD section 5.4a)
    prior_mass_pcts: np.ndarray | None = None       # (3, n_fac)
    prior_intensity_pcts: np.ndarray | None = None  # (3, n_fac)
    evidence: np.ndarray | None = None              # (3, n_fac) usable snapshots, survey visits, CMS usable hours

    def quantiles(self, kpi: str = "mass") -> np.ndarray | None:
        return self.mass_quantiles if kpi == "mass" else self.intensity_quantiles

    def prob_below(self, B: float, kpi: str = "mass") -> np.ndarray:
        """P(K <= B | data) per facility by linear interpolation on the quantile grid; nan where unscored."""
        q = self.quantiles(kpi)
        if q is None:
            raise ValueError("posterior quantile grid not available")
        out = np.full(self.n_fac, np.nan)
        for i in range(self.n_fac):
            col = q[:, i]
            if not np.isfinite(col).all():
                continue
            out[i] = np.interp(B, col, QGRID / 100.0, left=0.0, right=1.0) if col[-1] > col[0] else float(B >= col[0])
        return out

    def prior_interval(self, kpi: str = "mass") -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        pr = self.prior_mass_pcts if kpi == "mass" else self.prior_intensity_pcts
        return None if pr is None else (pr[0], pr[1], pr[2])

    def evidence_ratio(self, kpi: str = "mass") -> np.ndarray:
        """Posterior interval width as a fraction of the prior's, both measured as ln(p95 / p05) (PRD section 5.4a).

        Log widths make the ratio scale-free: a posterior that moved far into the prior's tail is
        judged by how much it narrowed, not by its absolute span. 1 = the data did not narrow the interval.
        """
        pr = self.prior_interval(kpi)
        if pr is None:
            return np.full(self.n_fac, np.nan)
        lo, _, hi = self.interval(kpi)
        return log_width_ratio(lo, hi, pr[0], pr[2])

    def interval(self, kpi: str = "mass") -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(lower, median, upper) of the two-sided 90 % credible interval."""
        if kpi == "mass":
            return self.mass_kg_yr_p05, self.mass_kg_yr_p50, self.mass_kg_yr_p95
        return self.intensity_p05, self.intensity_p50, self.intensity_p95

    def relative_half_width(self, kpi: str = "mass") -> np.ndarray:
        """w = (U - L) / (2 median) with [L, U] the two-sided 90 % interval (PRD section 5.4 as amended)."""
        lo, med, hi = self.interval(kpi)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(med > 0, (hi - lo) / (2 * med), np.inf)

    def covers(self, truth: np.ndarray, kpi: str = "mass") -> np.ndarray:
        lo, _, hi = self.interval(kpi)
        return (lo <= truth) & (truth <= hi)


@dataclass
class PriorDraws:
    omega: np.ndarray      # (n, K_MAX) 0/1
    z: np.ndarray          # (n, K_MAX)
    q: np.ndarray          # (n, K_MAX) kg/h
    pi: np.ndarray         # (n, K_MAX)
    e_on: np.ndarray       # (n, K_MAX) E[D_on] hours (for the CMS run-length term)
    e_off: np.ndarray | None = None    # (n, K_MAX) E[D_off]
    var_on: np.ndarray | None = None   # (n, K_MAX) Var[D_on]
    var_off: np.ndarray | None = None  # (n, K_MAX) Var[D_off]

    def mass_kg_yr(self) -> np.ndarray:
        """Expected annual mass sum_j omega_j pi_j q_j T (TDD section 6.7 as written)."""
        return (self.omega * self.pi * self.q).sum(axis=1) * HOURS_PER_YEAR

    def realised_mass_kg_yr(self, rng: np.random.Generator) -> np.ndarray:
        """Posterior predictive of the *realised* annual mass (DECISION_LOG 2026-09-30, Phase 4).

        For each intermittent source the annual on-hours H_j is random given (pi_j, durations).
        By the renewal-reward central limit theorem, Var[H_j] ~= T ((1-pi)^2 Var[D_on] + pi^2 Var[D_off]) / (E[D_on] + E[D_off])
        [renewal-theory]. H_j is drawn from a lognormal with mean pi_j T and that variance (capturing the
        right skew of a sum of few lognormal durations), clipped to [0, T]. Steady sources have H_j = T.
        """
        n, k = self.q.shape
        mean = self.pi * HOURS_PER_YEAR
        if self.e_off is None or self.var_on is None or self.var_off is None:
            return self.mass_kg_yr()
        cyc = np.maximum(self.e_on + self.e_off, 1e-9)
        var = HOURS_PER_YEAR * ((1 - self.pi) ** 2 * self.var_on + self.pi**2 * self.var_off) / cyc
        inter = (self.z == 1) & np.isfinite(var) & (var > 0)
        s2 = np.log1p(np.where(inter, var / np.maximum(mean, 1e-9) ** 2, 0.0))
        m = np.log(np.maximum(mean, 1e-9)) - 0.5 * s2
        h = np.where(inter, np.exp(m + np.sqrt(s2) * rng.standard_normal((n, k))), HOURS_PER_YEAR)
        h = np.clip(h, 0.0, HOURS_PER_YEAR)
        return (self.omega * h * self.q).sum(axis=1)

    def mean_pi_included(self) -> np.ndarray:
        """Rate-weighted duty cycle of the facility: sum omega pi q / sum omega q, i.e. the fraction of the facility's
        emitting capacity that is on. (Changed 2026-10-01 from the unweighted mean over candidate sources, which prior-only
        small sources dilute; the rate-weighted form is what continuous monitors constrain.)"""
        w = self.omega * self.q
        return (w * self.pi).sum(axis=1) / np.maximum(w.sum(axis=1), 1e-12)


def draw_prior(rng: np.random.Generator, sp: StratumPriors, n: int, k_max: int = K_MAX) -> PriorDraws:
    """Draws from the stratum prior (TDD sections 3.2-3.4 as the prior of section 6.2)."""
    K = np.minimum(rng.poisson(sp.lambda_k, size=n) + 1, k_max)                 # TDD section 11 item 7: cap at K_max
    omega = (np.arange(k_max)[None, :] < K[:, None]).astype(float)
    z = (rng.random((n, k_max)) < sp.p_intermittent).astype(np.int8)
    mu, sigma = rate_params_for_type(z, np.full((n, k_max), sp.mu_0), np.full((n, k_max), sp.sigma_0),
                                     np.full((n, k_max), sp.mu_1), np.full((n, k_max), sp.sigma_1))
    q = draw_rates_kg_h(rng, mu.ravel(), sigma.ravel(), np.full(n * k_max, sp.q_tail), np.full(n * k_max, sp.alpha)).reshape(n, k_max)
    nu_on = sp.nu_on + rng.normal(0.0, 1.0, size=(n, k_max)) * sp.sigma_nu_on
    nu_off = sp.nu_off + rng.normal(0.0, 1.0, size=(n, k_max)) * sp.sigma_nu_off
    pi_int = duty_cycle(nu_on, np.full((n, k_max), sp.tau_on), nu_off, np.full((n, k_max), sp.tau_off))
    pi = np.where(z == 1, pi_int, 1.0)
    e_on = np.where(z == 1, lognormal_mean(nu_on, sp.tau_on), np.inf)
    pd = PriorDraws(omega, z, q, pi, e_on)
    set_duration_moments(pd, nu_on, nu_off, sp)
    return pd


def set_duration_moments(pd: PriorDraws, nu_on: np.ndarray, nu_off: np.ndarray, sp: StratumPriors) -> None:
    """Fill E[D], Var[D] of both duration laws from nu, tau (lognormal moments) for the realised-mass predictive."""
    pd.e_on = np.where(pd.z == 1, lognormal_mean(nu_on, sp.tau_on), np.inf)
    pd.e_off = lognormal_mean(nu_off, sp.tau_off)
    pd.var_on = (np.exp(sp.tau_on**2) - 1.0) * np.exp(2 * nu_on + sp.tau_on**2)
    pd.var_off = (np.exp(sp.tau_off**2) - 1.0) * np.exp(2 * nu_off + sp.tau_off**2)


def _weighted_percentiles(x: np.ndarray, w: np.ndarray, ps: tuple[float, ...]) -> np.ndarray:
    order = np.argsort(x)
    cw = np.cumsum(w[order]); cw /= cw[-1]
    return np.interp(np.asarray(ps) / 100.0, cw, x[order])


@dataclass
class Ablation:
    """Oracle overrides for the variance budget (TDD section 6.8). All optional."""

    sigma_quant: float | None = None        # override every sensor's sigma (0 -> floor 0.05)
    pi_fixed: np.ndarray | None = None      # fix pi of included intermittent sources to these values (per candidate index)
    lam_fp_zero: bool = False
    sigma_g_zero: bool = False
    extra_source_obs: list[tuple[float, float]] | None = None   # oracle (q_true, sigma) observations for revealed small sources
    realised: bool | None = None            # override the realised-mass predictive (False = oracle knows the on-hours)


def make_facility_loglik(i: int, inputs: EstimatorInputs, sparams: list[SensorParams], ablation: Ablation | None = None):
    """Build ``loglik(draws) -> log L per draw`` for facility ``i`` (TDD sections 6.3-6.6)."""
    ab = ablation or Ablation()
    rho = float(inputs.rho_surf[i])
    sps = sparams
    if ab.sigma_quant is not None:
        from dataclasses import replace
        sps = [replace(s, sigma=max(ab.sigma_quant, 0.05)) for s in sps]
    if ab.lam_fp_zero:
        from dataclasses import replace
        sps = [replace(s, lam_fp=0.0) for s in sps]
    lam_override = 0.0 if ab.lam_fp_zero else None
    sn, sv, cm = inputs.snapshots, inputs.surveys, inputs.cms
    g0, g1 = sn.nd_offset[i], sn.nd_offset[i + 1]
    d0, d1 = sn.d_offset[i], sn.d_offset[i + 1]
    v0, v1 = sv.v_offset[i], sv.v_offset[i + 1]
    cms_rows = np.nonzero(cm.facility == i)[0]

    def loglik(pd: PriorDraws) -> np.ndarray:
        n = pd.q.shape[0]
        logL = np.zeros(n)
        pi = pd.pi
        if ab.pi_fixed is not None:
            fixed = np.broadcast_to(ab.pi_fixed, (K_MAX,))          # NaN = no oracle value for this candidate: keep the draw
            pi = np.where((pd.z == 1) & (pd.omega > 0) & np.isfinite(fixed)[None, :], np.clip(np.nan_to_num(fixed, nan=1.0)[None, :], 1e-6, 1.0), pd.pi)
        if g1 > g0 or d1 > d0:
            en = enumerate_states(pd.q, pi, pd.omega)
            for g in range(g0, g1):
                logL += snapshot_nd_loglik(en, sps[sn.nd_sensor[g]], float(sn.nd_wind[g]), rho, int(sn.nd_count[g]))
            for d in range(d0, d1):
                logL += snapshot_d_loglik(en, sps[sn.d_sensor[d]], float(sn.d_wind[d]), rho, float(sn.d_rate[d]), lam_fp=lam_override)
        for v in range(v0, v1):
            rates = sv.r_rate[sv.r_offset[v]:sv.r_offset[v + 1]]
            logL += survey_loglik(pd.q, pi, pd.omega, sps[sv.v_sensor[v]], float(sv.v_wind[v]), rho, rates, lam_fp=lam_override)
        for c in cms_rows:
            logL += cms_loglik(pd.q, pi, pd.omega, pd.e_on, sps[cm.sensor[c]], int(cm.n_usable[c]), int(cm.n_detected[c]),
                               int(cm.n_runs_det[c]), int(cm.n_runs_nd[c]), float(cm.mean_run_det[c]), float(cm.mean_ln_rate[c]))
        if ab.extra_source_obs:
            from scipy.special import logsumexp
            from scipy.stats import norm
            for q_true, sig in ab.extra_source_obs:
                lnq = np.log(np.where(pd.q > 0, pd.q, 1.0))
                term = norm.logpdf(np.log(q_true), loc=lnq, scale=sig) + np.where(pd.omega > 0, 0.0, -np.inf)
                logL += logsumexp(term, axis=1) - np.log(np.maximum(pd.omega.sum(axis=1), 1))
        return np.where(np.isfinite(logL), logL, -np.inf)

    return loglik


def facility_priors(i: int, inputs: EstimatorInputs, priors_by_stratum: list[StratumPriors]) -> StratumPriors:
    """The prior of facility ``i``: its stratum's, with the equipment-model overrides where the facility is a real site."""
    sp = priors_by_stratum[int(inputs.stratum_idx[i])]
    ov = inputs.prior_overrides
    if ov is None or not np.isfinite(ov["lambda_k"][i]):
        return sp
    return replace(sp, **{k: float(v[i]) for k, v in ov.items()})


def estimate_facility(i: int, inputs: EstimatorInputs, priors_by_stratum: list[StratumPriors], sparams: list[SensorParams],
                      seeds: SeedTree, n_draws: int, ablation: Ablation | None = None, method: str = "auto",
                      ess_switch: float = 200.0, smc_particles: int | None = None,
                      realised: bool = True) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float, dict]:
    """Posterior draws for one facility: (mass, intensity, mean pi, weights, ESS, info).

    ``method``: ``"is"`` plain importance sampling from the prior; ``"smc"`` tempered
    sequential importance sampling with resample-move (``mrvsim.estimate.smc``);
    ``"auto"`` runs IS and switches to SMC when the ESS falls below ``ess_switch``.
    ``realised`` reports the posterior predictive of the realised annual mass (default) rather than
    the expected mass sum pi q T; see ``PriorDraws.realised_mass_kg_yr``.
    """
    ab = ablation or Ablation()
    sp_h = facility_priors(i, inputs, priors_by_stratum)
    loglik = make_facility_loglik(i, inputs, sparams, ab)
    info: dict = {"method": "is"}
    if method in ("is", "auto"):
        rng = seeds.rng("estimate", "prior", facility=i)
        pd = draw_prior(rng, sp_h, n_draws)
        logL = loglik(pd)
        lw = logL - logL.max() if np.isfinite(logL.max()) else np.zeros(n_draws)
        w = np.exp(lw); w /= w.sum()
        ess = float(1.0 / np.sum(w**2))
    if method == "smc" or (method == "auto" and ess < ess_switch):
        from mrvsim.estimate.smc import smc_posterior
        n_p = smc_particles or max(n_draws // 4, 500)
        res = smc_posterior(loglik, sp_h, seeds.rng("estimate", "smc", facility=i), n_p)
        pd, w, ess = res.draws, res.weights, res.ess
        info = {"method": "smc", "stages": res.n_stages, "acceptance": res.acceptance, "particles": n_p}
    if ab.pi_fixed is not None:
        fixed = np.broadcast_to(ab.pi_fixed, (K_MAX,))
        pd.pi = np.where((pd.z == 1) & (pd.omega > 0) & np.isfinite(fixed)[None, :], np.clip(np.nan_to_num(fixed, nan=1.0)[None, :], 1e-6, 1.0), pd.pi)
    sig_g = 0.0 if ab.sigma_g_zero else float(inputs.sigma_g[i])
    G = denominator_draws(seeds.rng("estimate", "denominator", facility=i), float(inputs.g_hat_kg_yr[i]), sig_g, pd.q.shape[0])
    use_realised = realised if ab.realised is None else ab.realised
    mass = pd.realised_mass_kg_yr(seeds.rng("estimate", "realisation", facility=i)) if use_realised else pd.mass_kg_yr()
    intensity = float(inputs.f_gas[i]) * mass / G
    return mass, intensity, pd.mean_pi_included(), w, ess, info


def prior_summary(i: int, inputs: EstimatorInputs, sp_h: StratumPriors, seeds: SeedTree, n_draws: int,
                  realised: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Prior (no-observation) p05/p50/p95 of realised mass and intensity for facility ``i``.

    Uses the same seed keys as the importance-sampling path, so these are the unweighted
    percentiles of the IS draw set (common random numbers) at no likelihood cost. The
    posterior/prior width ratio is the facility's evidence measure (PRD section 5.4a).
    """
    pd = draw_prior(seeds.rng("estimate", "prior", facility=i), sp_h, n_draws)
    G = denominator_draws(seeds.rng("estimate", "denominator", facility=i), float(inputs.g_hat_kg_yr[i]), float(inputs.sigma_g[i]), n_draws)
    mass = pd.realised_mass_kg_yr(seeds.rng("estimate", "realisation", facility=i)) if realised else pd.mass_kg_yr()
    inten = float(inputs.f_gas[i]) * mass / G
    u = np.full(n_draws, 1.0 / n_draws)   # same percentile convention as the posterior so no-data runs give ratio exactly 1
    return _weighted_percentiles(mass, u, (5, 50, 95)), _weighted_percentiles(inten, u, (5, 50, 95))


def run_fast_estimator(inputs: EstimatorInputs, strata: StrataTable, priors: PriorSet, library: SensorLibrary, seeds: SeedTree,
                       n_draws: int = 10_000, facilities: np.ndarray | None = None, ablation: Ablation | None = None,
                       ess_warn: float = 100.0, method: str = "auto", ess_switch: float = 200.0, realised: bool = True) -> PosteriorSummary:
    """Fast path over all (or selected) facilities (TDD section 6.7)."""
    priors_by_stratum = [priors.for_cell(s.basin, s.facility_type) for s in strata.strata]
    sparams = [SensorParams.from_sensor(library[k]) for k in inputs.sensor_keys]
    idx = np.arange(inputs.n_fac) if facilities is None else np.asarray(facilities, dtype=np.int64)
    n = inputs.n_fac
    P = {k: np.full(n, np.nan) for k in ("p10", "p50", "p90", "ess")}
    MQ = np.full((QGRID.size, n), np.nan); IQ = np.full((QGRID.size, n), np.nan)
    PM = np.full((3, n), np.nan); PI = np.full((3, n), np.nan)
    n_smc = 0
    for i in idx:
        mass, inten, pim, w, ess, info = estimate_facility(int(i), inputs, priors_by_stratum, sparams, seeds, n_draws, ablation,
                                                           method=method, ess_switch=ess_switch, realised=realised)
        n_smc += info["method"] == "smc"
        MQ[:, i] = _weighted_percentiles(mass, w, tuple(QGRID))
        IQ[:, i] = _weighted_percentiles(inten, w, tuple(QGRID))
        P["p10"][i], P["p50"][i], P["p90"][i] = _weighted_percentiles(pim, w, (10, 50, 90))
        P["ess"][i] = ess
        PM[:, i], PI[:, i] = prior_summary(int(i), inputs, facility_priors(int(i), inputs, priors_by_stratum), seeds, n_draws, realised)
    low_ess = idx[P["ess"][idx] < ess_warn]
    row = lambda Q, p: Q[int(p)]  # noqa: E731  QGRID is 0..100 in steps of 1
    return PosteriorSummary(n, row(MQ, 10), row(MQ, 50), row(MQ, 90), row(IQ, 10), row(IQ, 50), row(IQ, 90), P["p10"], P["p50"], P["p90"], P["ess"],
                            method="fast", n_draws=n_draws,
                            meta={"n_low_ess": int(low_ess.size), "low_ess_facilities": low_ess[:50].tolist(), "ess_warn": ess_warn,
                                  "n_smc": int(n_smc), "sampler": method, "estimand": "realised" if realised else "expected"},
                            mass_kg_yr_p05=row(MQ, 5), mass_kg_yr_p95=row(MQ, 95), intensity_p05=row(IQ, 5), intensity_p95=row(IQ, 95),
                            mass_quantiles=MQ, intensity_quantiles=IQ, prior_mass_pcts=PM, prior_intensity_pcts=PI,
                            evidence=evidence_counts(inputs))
