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

_PRIORS_DIR = Path(__file__).resolve().parents[2] / "configs" / "priors"
CLASSES = ("drygas", "gaswoil", "oilwgas", "oilonly")
M3_PER_MSCF = 28.316847
# Expected sources per site are capped so that the estimator's K_MAX (TDD section 11 item 7) rarely binds:
# with K = Poisson(lambda) + 1 and lambda + 1 = 4, P(K > 8) is about 1 %.
MAX_EXPECTED_SOURCES = 4.0
MAX_DUTY_CYCLE = 0.5            # pooled episodic emitters cannot be on more than this share of the time
NO_PARETO_SPLICE_KG_H = 1.0e12  # q_tail for equipment-model sites: the lognormal is not spliced (see site_prior_params)
OVERRIDE_KEYS = ("lambda_k", "p_intermittent", "mu_0", "sigma_0", "mu_1", "sigma_1", "q_tail", "nu_off")
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


def expected_duty_cycle(nu_on: np.ndarray, tau_on: np.ndarray, nu_off: np.ndarray, tau_off: np.ndarray,
                        sigma_nu_on: np.ndarray, sigma_nu_off: np.ndarray) -> np.ndarray:
    """E[pi] over the between-source spread of the duration locations (pi as in TDD section 3.4)."""
    x_on = np.asarray(nu_on, float)[..., None, None] + np.asarray(sigma_nu_on, float)[..., None, None] * _GH_X[:, None]
    x_off = np.asarray(nu_off, float)[..., None, None] + np.asarray(sigma_nu_off, float)[..., None, None] * _GH_X[None, :]
    e_on = np.exp(x_on + 0.5 * np.asarray(tau_on, float)[..., None, None] ** 2)
    e_off = np.exp(x_off + 0.5 * np.asarray(tau_off, float)[..., None, None] ** 2)
    return ((e_on / (e_on + e_off)) * (_GH_W[:, None] * _GH_W[None, :])).sum(axis=(-2, -1))


def site_prior_params(cells: EquipmentCells, cls: np.ndarray, bin_: np.ndarray, n_wells: np.ndarray,
                      base: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
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

    The Pareto splice of TDD section 3.3 is switched off for these sites (q_tail set very high): it would add
    mass that the equipment data do not contain. The aerial-survey tail is a separate, still open, add-on.
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
    pi_target = np.minimum(g * pi_base, MAX_DUTY_CYCLE)
    # Shift nu_off so the duty cycle at the stratum's central durations scales by pi_target / pi_base, then
    # take the expectation again: the rate below uses the duty cycle the generator will actually produce.
    e_on = np.exp(base["nu_on"] + 0.5 * base["tau_on"] ** 2)
    pi_central = e_on / (e_on + np.exp(base["nu_off"] + 0.5 * base["tau_off"] ** 2))
    pi_new_central = np.clip(pi_central * pi_target / pi_base, 1e-6, 0.999)
    nu_off = np.log(e_on * (1.0 - pi_new_central) / pi_new_central) - 0.5 * base["tau_off"] ** 2
    pi_exp = expected_duty_cycle(base["nu_on"], base["tau_on"], nu_off, base["tau_off"], base["sigma_nu_on"], base["sigma_nu_off"])
    sigma_1 = cells.episodic_sigma[cls, bin_]
    mu_1 = np.log(g * cells.episodic_mean_kg_h[cls, bin_] / pi_exp) - 0.5 * sigma_1**2

    return {"lambda_k": k_exp - 1.0, "p_intermittent": n_e / (n_s + n_e), "mu_0": mu_0, "sigma_0": sigma_0, "mu_1": mu_1, "sigma_1": sigma_1,
            "q_tail": np.full(n.shape, NO_PARETO_SPLICE_KG_H), "nu_off": nu_off}
