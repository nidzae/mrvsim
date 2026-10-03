"""Throughput and denominator (TDD section 3.5; PRD section 5.1).

G_i = V_gas,mkt,i * rho_CH4 * X_CH4,i, with ln X_CH4 ~ N(ln 0.88, 0.05^2).
V_gas,mkt is drawn from the cell's lognormal, truncated to the facility's
throughput class (tercile or quintile band) by inverse-CDF sampling, which is
what makes the classes quantiles of the cell distribution by construction.
Oil production gives the energy-share allocation f_gas (PRD section 5.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import yaml
from scipy.special import ndtri

MIN_GAS_M3_YR = 1.0             # numerical floor for real sites that report no gas (see draw_throughput)
DEFAULT_CONSTANTS_PATH = Path(__file__).resolve().parents[2] / "configs" / "constants.yaml"


@dataclass(frozen=True)
class Constants:
    hours_per_year: int
    rho_ch4_kg_per_m3: float        # [ngsi]
    x_ch4_default: float            # TDD section 3.5
    x_ch4_log_sd: float
    gas_hhv_mj_per_m3: float        # [eia-heat-content] TODO(verify)
    oil_mj_per_bbl: float           # [eia-heat-content] TODO(verify)
    mmbtu_per_mj: float
    completeness_threshold_kg_h: float  # [jacob2022]

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CONSTANTS_PATH) -> "Constants":
        raw: Mapping[str, Any] = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(
            hours_per_year=int(raw["hours_per_year"]),
            rho_ch4_kg_per_m3=float(raw["rho_ch4_kg_per_m3"]),
            x_ch4_default=float(raw["x_ch4_default"]),
            x_ch4_log_sd=float(raw["x_ch4_log_sd"]),
            gas_hhv_mj_per_m3=float(raw["gas_hhv_mj_per_m3"]),
            oil_mj_per_bbl=float(raw["oil_mj_per_bbl"]),
            mmbtu_per_mj=float(raw["mmbtu_per_mj"]),
            completeness_threshold_kg_h=float(raw["completeness_threshold_kg_h"]),
        )


def draw_class_truncated_lognormal(
    rng: np.random.Generator, mu: np.ndarray, sigma: np.ndarray, class_index: np.ndarray, n_classes: np.ndarray
) -> np.ndarray:
    """Lognormal draw restricted to quantile band [k/N, (k+1)/N) via inverse CDF.

    Guarantees that class k of a cell is exactly the k-th N-quantile band of the
    cell's throughput distribution (TDD section 3.1, throughput classes).
    """
    k = np.asarray(class_index, dtype=float)
    n = np.asarray(n_classes, dtype=float)
    u = (k + rng.random(k.shape)) / n
    # Deviation for numerical safety: keep u strictly inside (0, 1) so ndtri is finite.
    u = np.clip(u, 1e-12, 1 - 1e-12)
    return np.exp(np.asarray(mu, dtype=float) + np.asarray(sigma, dtype=float) * ndtri(u))


@dataclass
class Throughput:
    gas_mkt_m3_yr: np.ndarray       # V_gas,mkt
    oil_bbl_yr: np.ndarray
    x_ch4: np.ndarray               # mole fraction CH4 in marketed gas
    g_ch4_kg_yr: np.ndarray         # G_i, marketed methane mass
    f_gas: np.ndarray               # energy-share allocation to gas (PRD section 5.1)
    mmbtu_yr: np.ndarray            # marketed gas energy, for cost per certified MMBtu (PRD section 5.6)
    ghgrp_reporter: np.ndarray      # bool; sets sigma_G in the estimator (TDD section 6.6)


def draw_throughput(
    rng: np.random.Generator,
    ln_gas_mu: np.ndarray, ln_gas_sigma: np.ndarray,
    ln_oil_mu: np.ndarray, ln_oil_sigma: np.ndarray,
    class_index: np.ndarray, n_classes: np.ndarray,
    ghgrp_share: np.ndarray,
    constants: Constants,
    gas_m3_yr: np.ndarray | None = None,
    oil_bbl_yr: np.ndarray | None = None,
) -> Throughput:
    """Draw V_gas (class-truncated), oil, X_CH4, and derived G_i, f_gas, MMBtu (TDD section 3.5).

    ``gas_m3_yr`` / ``oil_bbl_yr`` give the real production of facilities that are real sites [ogim]
    (NaN elsewhere); those facilities keep it instead of the lognormal draw (TDD section 3.5 as amended 2026-10-03).
    """
    gas = draw_class_truncated_lognormal(rng, ln_gas_mu, ln_gas_sigma, class_index, n_classes)
    oil = np.exp(rng.normal(np.asarray(ln_oil_mu, dtype=float), np.asarray(ln_oil_sigma, dtype=float)))
    if gas_m3_yr is not None and oil_bbl_yr is not None:
        real = np.isfinite(gas_m3_yr)
        # Deviation from TDD section 3.5 for numerical reasons: a site that reports no gas gets MIN_GAS_M3_YR so
        # that ln G is finite. Intensity f_gas * M / G does not depend on the floor: as gas -> 0, f_gas / G tends
        # to HHV_gas / (E_oil * rho * X), the energy-allocated limit of PRD section 5.1.
        gas = np.where(real, np.maximum(gas_m3_yr, MIN_GAS_M3_YR), gas)
        oil = np.where(real, oil_bbl_yr, oil)
    x = np.exp(rng.normal(np.log(constants.x_ch4_default), constants.x_ch4_log_sd, size=gas.shape))
    # Deviation from TDD section 3.5 for physical validity: mole fraction cannot exceed 1.
    x = np.minimum(x, 1.0)
    g = gas * constants.rho_ch4_kg_per_m3 * x
    e_gas = gas * constants.gas_hhv_mj_per_m3
    e_oil = oil * constants.oil_mj_per_bbl
    f_gas = e_gas / (e_gas + e_oil)
    mmbtu = e_gas * constants.mmbtu_per_mj
    reporter = rng.random(gas.shape) < np.asarray(ghgrp_share, dtype=float)
    return Throughput(gas, oil, x, g, f_gas, mmbtu, reporter)
