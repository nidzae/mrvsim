"""Equipment-based leak model for real production sites (TDD sections 3.2-3.4 as amended 2026-10-03).

A real site's source-count, rate and duty-cycle hyperparameters follow from its public covariates:
well count, well class (oil / gas mix) and per-well productivity bin. The per-well numbers come from
``configs/priors/equipment_cells_<date>.yaml``, fitted by ``data/scripts/fit_equipment_priors.py`` to the
component-based model of [rutherford2021]. The result is a set of per-facility overrides of the stratum
priors, used identically by the population generator (truth) and the estimator (prior).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import yaml
from scipy.special import log_ndtr, ndtr, ndtri

_PRIORS_DIR = Path(__file__).resolve().parents[2] / "configs" / "priors"
CLASSES = ("drygas", "gaswoil", "oilwgas", "oilonly")
M3_PER_MSCF = 28.316847
# Expected sources per site are capped so that the estimator's K_MAX (TDD section 11 item 7) rarely binds:
# with K = Poisson(lambda) + 1 and lambda + 1 = 4, P(K > 8) is about 1 %.
MAX_EXPECTED_SOURCES = 4.0
MAX_DUTY_CYCLE = 0.5            # pooled episodic emitters cannot be on more than this share of the time
NO_PARETO_SPLICE_KG_H = 1.0e12  # q_tail for equipment-model sites: the lognormal is not spliced (see site_prior_params)
OVERRIDE_KEYS = ("lambda_k", "p_intermittent", "mu_0", "sigma_0", "mu_1", "sigma_1", "q_tail", "alpha", "nu_off")
MAX_TAIL_SHARE = 0.3            # at most this share of an intermittent source's on-time is spent above the transition point
_GH_X, _GH_W = np.polynomial.hermite_e.hermegauss(9)      # Gauss-Hermite nodes for N(0, 1) expectations
_GH_W = _GH_W / _GH_W.sum()


def default_equipment_path() -> Path | None:
    files = sorted(_PRIORS_DIR.glob("equipment_cells_*.yaml"))
    return files[-1] if files else None


@dataclass(frozen=True)
class EquipmentCells:
    """Per-well parameters indexed [class, bin] (NaN where a class has fewer bins) [rutherford2021]."""

    provenance: str
    source_path: str
    gas_bin_edges_mscf_d: np.ndarray
    oil_bin_edges_bbl_d: np.ndarray
    gor_cutoff_mscf_per_bbl: float
    k_steady: np.ndarray            # emitting steady equipment categories per well
    steady_mean_kg_h: np.ndarray    # mean emission of one emitting steady category
    steady_sigma: np.ndarray        # lognormal sigma of that emission
    k_episodic: np.ndarray
    episodic_mean_kg_h: np.ndarray  # annual-average emission of one emitting episodic category
    episodic_sigma: np.ndarray
    citation_keys: tuple[str, ...] = ("rutherford2021",)

    def classify(self, gas_m3_yr: np.ndarray, oil_bbl_yr: np.ndarray, n_wells: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Well class and productivity bin of each site, by the rules of [rutherford2021] (tranche_data.m)."""
        gas = np.asarray(gas_m3_yr, float) / M3_PER_MSCF / 365.25          # Mscf/day
        oil = np.asarray(oil_bbl_yr, float) / 365.25                        # bbl/day
        n = np.maximum(np.asarray(n_wells, float), 1.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            gor = np.where(oil > 0, gas / oil, np.inf)
        cls = np.select([gas <= 0, oil <= 0, gor > self.gor_cutoff_mscf_per_bbl], [3, 0, 1], 2)
        gas_bin = np.searchsorted(self.gas_bin_edges_mscf_d, gas / n, side="right")
        oil_bin = np.searchsorted(self.oil_bin_edges_bbl_d, oil / n, side="right")
        return cls.astype(np.int64), np.where(cls == 3, oil_bin, gas_bin).astype(np.int64)


def load_equipment_cells(path: str | Path | None = None) -> EquipmentCells | None:
    path = Path(path) if path is not None else default_equipment_path()
    if path is None or not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    n_bins = max(len(v) for v in raw["cells"].values())
    arr = {k: np.full((len(CLASSES), n_bins), np.nan) for k in ("ks", "ms", "ss", "ke", "me", "se")}
    for ci, c in enumerate(CLASSES):
        for cell in raw["cells"][c]:
            b = int(cell["bin"]); s, e = cell["steady"], cell["episodic"]
            arr["ks"][ci, b], arr["ms"][ci, b], arr["ss"][ci, b] = s["emitters_per_well"], s["mean_kg_h"], s["sigma"]
            arr["ke"][ci, b], arr["me"][ci, b], arr["se"][ci, b] = e["emitters_per_well"], e["mean_kg_h"], e["sigma"]
    return EquipmentCells(
        provenance=str(raw.get("provenance", "UNKNOWN")), source_path=str(path),
        gas_bin_edges_mscf_d=np.asarray(raw["gas_bin_edges_mscf_per_well_day"], float),
        oil_bin_edges_bbl_d=np.asarray(raw["oil_bin_edges_bbl_per_well_day"], float),
        gor_cutoff_mscf_per_bbl=float(raw["gor_cutoff_mscf_per_bbl"]),
        k_steady=arr["ks"], steady_mean_kg_h=arr["ms"], steady_sigma=arr["ss"],
        k_episodic=arr["ke"], episodic_mean_kg_h=arr["me"], episodic_sigma=arr["se"],
        citation_keys=tuple(raw.get("citation_keys", ("rutherford2021",))),
    )


@dataclass(frozen=True)
class AerialTail:
    """Per-basin super-emitter tail above the aerial transition point [sherwin2024] (fit_aerial_tail.py)."""

    provenance: str
    source_path: str
    transition_kg_h: Mapping[str, float]
    alpha: Mapping[str, float]
    p_per_well: Mapping[str, float]
    frequency_max: float
    citation_keys: tuple[str, ...] = ("sherwin2024",)

    def site_tail(self, basin: np.ndarray, ch4_kg_h: np.ndarray, n_wells: np.ndarray) -> dict[str, np.ndarray]:
        """Transition point, Pareto index and snapshot frequency of an emission above the transition point, per site.

        A site is eligible only if it produces at least the transition point in methane; the frequency is
        n_wells x the basin's per-well chance (capped), zero otherwise.
        """
        T = np.array([self.transition_kg_h.get(b, np.inf) for b in basin])
        a = np.array([self.alpha.get(b, np.nan) for b in basin])
        p = np.array([self.p_per_well.get(b, 0.0) for b in basin])
        eligible = np.asarray(ch4_kg_h, float) >= T
        f = np.where(eligible & np.isfinite(p), np.minimum(np.asarray(n_wells, float) * p, self.frequency_max), 0.0)
        return {"T": T, "alpha": a, "freq": f}


def default_tail_path() -> Path | None:
    files = sorted(_PRIORS_DIR.glob("aerial_tail_*.yaml"))
    return files[-1] if files else None


def load_aerial_tail(path: str | Path | None = None) -> AerialTail | None:
    path = Path(path) if path is not None else default_tail_path()
    if path is None or not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    b = raw["basins"]
    return AerialTail(provenance=str(raw.get("provenance", "UNKNOWN")), source_path=str(path),
                      transition_kg_h={k: float(v["transition_kg_h"]) for k, v in b.items()}, alpha={k: float(v["alpha"]) for k, v in b.items()},
                      p_per_well={k: float(v["p_per_well"]) for k, v in b.items()}, frequency_max=float(raw.get("frequency_max", 0.5)),
                      citation_keys=tuple(raw.get("citation_keys", ("sherwin2024",))))


def expected_duty_cycle(nu_on: np.ndarray, tau_on: np.ndarray, nu_off: np.ndarray, tau_off: np.ndarray,
                        sigma_nu_on: np.ndarray, sigma_nu_off: np.ndarray) -> np.ndarray:
    """E[pi] over the between-source spread of the duration locations (pi as in TDD section 3.4)."""
    x_on = np.asarray(nu_on, float)[..., None, None] + np.asarray(sigma_nu_on, float)[..., None, None] * _GH_X[:, None]
    x_off = np.asarray(nu_off, float)[..., None, None] + np.asarray(sigma_nu_off, float)[..., None, None] * _GH_X[None, :]
    e_on = np.exp(x_on + 0.5 * np.asarray(tau_on, float)[..., None, None] ** 2)
    e_off = np.exp(x_off + 0.5 * np.asarray(tau_off, float)[..., None, None] ** 2)
    return ((e_on / (e_on + e_off)) * (_GH_W[:, None] * _GH_W[None, :])).sum(axis=(-2, -1))


def _nu_off_for_duty(base: Mapping[str, np.ndarray], pi_base: np.ndarray, pi_target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Shift nu_off so the expected duty cycle moves from pi_base towards pi_target; returns (nu_off, achieved E[pi])."""
    e_on = np.exp(base["nu_on"] + 0.5 * base["tau_on"] ** 2)
    pi_central = e_on / (e_on + np.exp(base["nu_off"] + 0.5 * base["tau_off"] ** 2))
    pi_new_central = np.clip(pi_central * pi_target / pi_base, 1e-6, 0.999)
    nu_off = np.log(e_on * (1.0 - pi_new_central) / pi_new_central) - 0.5 * base["tau_off"] ** 2
    for _ in range(3):      # the between-source spread makes E[pi] differ from the central value: correct towards the target
        got = expected_duty_cycle(base["nu_on"], base["tau_on"], nu_off, base["tau_off"], base["sigma_nu_on"], base["sigma_nu_off"])
        pi_new_central = np.clip(pi_new_central * pi_target / got, 1e-6, 0.999)
        nu_off = np.log(e_on * (1.0 - pi_new_central) / pi_new_central) - 0.5 * base["tau_off"] ** 2
    return nu_off, expected_duty_cycle(base["nu_on"], base["tau_on"], nu_off, base["tau_off"], base["sigma_nu_on"], base["sigma_nu_off"])


def _sigma_for_mass_below(ln_T: np.ndarray, z_t: np.ndarray, ln_c: np.ndarray, fallback: np.ndarray) -> np.ndarray:
    """sigma of a lognormal with P(q > T) fixed (mu = ln T - sigma z_t) whose mean mass below T, E[q; q < T], is c.

    h(sigma) = ln T - sigma z_t + sigma^2 / 2 + ln Phi(z_t - sigma) - ln c is decreasing in sigma; bisection.
    """
    h = lambda sg: ln_T - sg * z_t + 0.5 * sg**2 + log_ndtr(z_t - sg) - ln_c  # noqa: E731
    lo, hi = np.full(ln_T.shape, 0.02), np.full(ln_T.shape, 6.0)
    solvable = (h(lo) > 0) & (h(hi) < 0)
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        up = h(mid) > 0
        lo, hi = np.where(up, mid, lo), np.where(up, hi, mid)
    return np.where(solvable, 0.5 * (lo + hi), fallback)


def site_prior_params(cells: EquipmentCells, cls: np.ndarray, bin_: np.ndarray, n_wells: np.ndarray,
                      base: Mapping[str, np.ndarray], tail: Mapping[str, np.ndarray] | None = None) -> dict[str, np.ndarray]:
    """Per-site overrides of the stratum priors (keys ``OVERRIDE_KEYS``).

    ``base`` holds the site's stratum duration parameters (``nu_on``, ``tau_on``, ``nu_off``, ``tau_off``,
    ``sigma_nu_on``, ``sigma_nu_off``), which this model keeps.

    A site with n wells holds N_s = n k_s steady and N_e = n k_e episodic emitters in expectation. They are
    represented by K = Poisson(lambda) + 1 site-level sources with lambda + 1 = min(N_s + N_e, MAX_EXPECTED_SOURCES),
    each standing for g = (N_s + N_e) / (lambda + 1) emitters, and p_intermittent = N_e / (N_s + N_e):

    - a steady source is the sum of g independent lognormal emitters, itself approximated by a lognormal with
      the same mean and variance (Fenton-Wilkinson moment matching), so the site's expected steady mass is exact;
    - an intermittent source pools g episodic emitters: the stratum's off-duration is shortened so that the duty
      cycle is g times the stratum's (capped at MAX_DUTY_CYCLE), and the rate when on is lognormal with the
      episodic emitter's sigma and the mean that makes rate x E[duty cycle] equal the pooled annual average.

    ``tail`` (from :meth:`AerialTail.site_tail`) adds the aerial-survey super-emitter tail [sherwin2024] to sites
    with a positive snapshot frequency f of an emission above the transition point T. It uses the Pareto splice
    of TDD section 3.3 on the intermittent sources: q_tail = T with the basin's alpha, and the lognormal body is
    re-solved so that (i) the chance that an intermittent source that is on emits above T reproduces f, and
    (ii) the mass emitted below T equals the equipment model's episodic mass from emitters whose annual-average
    rate is below T (nearly all of it). Following the survey's own construction, the bottom-up model is kept
    below T and the aerial data above it.
    The duty cycle is raised where needed so that at most MAX_TAIL_SHARE of on-time is above T. Expected site
    mass is then the equipment model's steady mass + its episodic mass below T + f T alpha / (alpha - 1).
    Sites without a tail keep an unspliced lognormal (q_tail set very high).
    """
    cls, bin_ = np.asarray(cls), np.asarray(bin_)
    n = np.maximum(np.asarray(n_wells, float), 1.0)
    n_s = n * cells.k_steady[cls, bin_]
    n_e = n * cells.k_episodic[cls, bin_]
    k_exp = np.clip(n_s + n_e, 1.0, MAX_EXPECTED_SOURCES)
    g = (n_s + n_e) / k_exp

    m0, s0 = cells.steady_mean_kg_h[cls, bin_], cells.steady_sigma[cls, bin_]
    var0 = (np.exp(s0**2) - 1.0) * m0**2
    sigma_0 = np.sqrt(np.log1p(var0 / (g * m0**2)))
    mu_0 = np.log(g * m0) - 0.5 * sigma_0**2

    pi_base = expected_duty_cycle(base["nu_on"], base["tau_on"], base["nu_off"], base["tau_off"], base["sigma_nu_on"], base["sigma_nu_off"])
    nu_off, pi_exp = _nu_off_for_duty(base, pi_base, np.minimum(g * pi_base, MAX_DUTY_CYCLE))
    sigma_1 = cells.episodic_sigma[cls, bin_].copy()
    mu_1 = np.log(g * cells.episodic_mean_kg_h[cls, bin_] / pi_exp) - 0.5 * sigma_1**2
    q_tail = np.full(n.shape, NO_PARETO_SPLICE_KG_H)
    alpha = np.full(n.shape, 2.0)                       # unused where the splice is off

    has_tail = np.zeros(n.shape, dtype=bool) if tail is None else (tail["freq"] > 0) & (n_e > 0)
    if has_tail.any():
        i = has_tail
        T, f = tail["T"][i], tail["freq"][i]
        b_i = {k: v[i] for k, v in base.items()}
        k_int = (k_exp * n_e / (n_s + n_e))[i]                                  # expected intermittent sources on the site
        a_epi = (n_e * cells.episodic_mean_kg_h[cls, bin_])[i]                  # episodic annual-average mass, kg/h
        # Share of that mass kept below T. As in the survey's construction, the bottom-up model is taken as annual-average
        # rates: the share is that of pooled emitters whose annual-average rate is below T (close to 1), not of
        # rates-when-on, which depend on the duty cycle assumed for episodic sources.
        mu_avg = np.log((g * cells.episodic_mean_kg_h[cls, bin_])[i]) - 0.5 * sigma_1[i] ** 2
        below = ndtr((np.log(T) - mu_avg - sigma_1[i] ** 2) / sigma_1[i])
        # expected on-fraction u = k_int E[pi]: enough for the tail frequency and for a mean rate below T under T / 2
        u = np.maximum.reduce([k_int * pi_exp[i], f / MAX_TAIL_SHARE, 2.0 * a_epi * below / T + f])
        nu_off_i, pi_i = _nu_off_for_duty(b_i, pi_base[i], np.minimum(u / k_int, MAX_DUTY_CYCLE))
        u = k_int * pi_i
        t = np.clip(f / u, 1e-9, 0.5)                                            # P(q > T | on)
        c = np.minimum(a_epi * below / u, 0.9 * T * (1.0 - t))                   # E[q; q < T | on]
        z_t = ndtri(1.0 - t)
        sig = _sigma_for_mass_below(np.log(T), z_t, np.log(c), sigma_1[i])
        nu_off[i], sigma_1[i], mu_1[i] = nu_off_i, sig, np.log(T) - sig * z_t
        q_tail[i], alpha[i] = T, tail["alpha"][i]

    return {"lambda_k": k_exp - 1.0, "p_intermittent": n_e / (n_s + n_e), "mu_0": mu_0, "sigma_0": sigma_0, "mu_1": mu_1, "sigma_1": sigma_1,
            "q_tail": q_tail, "alpha": alpha, "nu_off": nu_off}
