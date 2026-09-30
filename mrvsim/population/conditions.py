"""Observing conditions per facility (TDD section 3.6).

Monthly daytime cloud fraction [modis-cloud], SWIR surface reflectance and
heterogeneity [landsat-composite], and a Weibull wind-speed distribution per
month [era5]. Values come from the basin climatology in the priors file with a
small seeded facility-level jitter; when ``data/fitted/conditions.json`` exists
(written by the fetch scripts) it replaces the basin climatology.

Solar zenith angle at satellite overpasses is computed in ``mrvsim.observe``
from date, time, and coordinate; it is not a stored condition.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from mrvsim.population.priors import PriorSet


@dataclass
class Conditions:
    p_cloud: np.ndarray             # (n_fac, 12) daytime cloud fraction by month
    surface_reflectance: np.ndarray  # (n_fac,)
    surface_heterogeneity: np.ndarray  # (n_fac,) in [0, 1]
    wind_k: np.ndarray              # (n_fac,) Weibull shape
    wind_lambda: np.ndarray         # (n_fac, 12) Weibull scale, m/s

    def draw_wind_m_s(self, rng: np.random.Generator, facility_idx: np.ndarray, month_idx: np.ndarray) -> np.ndarray:
        """Wind speed draws for paired (facility, month) indices."""
        k = self.wind_k[facility_idx]
        lam = self.wind_lambda[facility_idx, month_idx]
        return lam * rng.weibull(k)

    def cloud_prob(self, facility_idx: np.ndarray, month_idx: np.ndarray) -> np.ndarray:
        return self.p_cloud[facility_idx, month_idx]


def draw_conditions(
    rng: np.random.Generator, basin_keys: list[str], facility_basin_idx: np.ndarray, priors: PriorSet,
    cloud_jitter_sd: float = 0.05, reflectance_jitter_logsd: float = 0.10, wind_jitter_logsd: float = 0.10,
) -> Conditions:
    """Facility conditions from basin climatology plus seeded jitter.

    Jitter magnitudes are PLACEHOLDER modelling choices (not fitted) that stand
    in for within-basin spatial variability until gridded data are attached.
    """
    n = facility_basin_idx.shape[0]
    cloud_b = np.array([priors.conditions_for(b).cloud_monthly for b in basin_keys])                # (B, 12)
    refl_b = np.array([priors.conditions_for(b).surface_reflectance for b in basin_keys])
    het_b = np.array([priors.conditions_for(b).surface_heterogeneity for b in basin_keys])
    k_b = np.array([priors.conditions_for(b).wind_weibull_k for b in basin_keys])
    lam_b = np.array([priors.conditions_for(b).wind_weibull_lambda_monthly for b in basin_keys])    # (B, 12)

    cloud = cloud_b[facility_basin_idx] + rng.normal(0.0, cloud_jitter_sd, size=(n, 1))
    cloud = np.clip(cloud, 0.0, 1.0)
    refl = refl_b[facility_basin_idx] * np.exp(rng.normal(0.0, reflectance_jitter_logsd, size=n))
    het = np.clip(het_b[facility_basin_idx] + rng.normal(0.0, 0.05, size=n), 0.0, 1.0)
    k = k_b[facility_basin_idx]
    lam = lam_b[facility_basin_idx] * np.exp(rng.normal(0.0, wind_jitter_logsd, size=(n, 1)))
    return Conditions(cloud, refl, het, k, lam)
