"""Emission rate of an asset's large source (SPEC section 6b).

- Two or more detections: lognormal fitted to the measured rates, each inflated by its reported uncertainty.
- One detection: that rate with its uncertainty.
- Zero detections: Q drawn from the size distribution of detected sources in the same basin and segment,
  truncated to the sensor's detection range (the "what could have been missed" draw).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class RateModel:
    mu: float               # ln kg/h
    sigma: float
    n_detections: int
    basis: str              # "fitted", "single", "basin_prior"
    lo_kg_h: float = 0.0    # truncation for basin priors
    hi_kg_h: float = np.inf

    def sample(self, rng: np.random.Generator, n: int) -> np.ndarray:
        q = np.exp(rng.normal(self.mu, self.sigma, size=n))
        if np.isfinite(self.hi_kg_h) or self.lo_kg_h > 0:
            bad = (q < self.lo_kg_h) | (q > self.hi_kg_h)
            for _ in range(20):
                if not bad.any():
                    break
                q[bad] = np.exp(rng.normal(self.mu, self.sigma, size=int(bad.sum())))
                bad = (q < self.lo_kg_h) | (q > self.hi_kg_h)
            q = np.clip(q, self.lo_kg_h, self.hi_kg_h)
        return q

    def median_kg_h(self) -> float:
        return float(np.exp(self.mu))


def rate_from_detections(rates_kg_h: np.ndarray, uncertainty_kg_h: np.ndarray | None, sensor_log_sigma: np.ndarray | float) -> RateModel:
    """Lognormal from measured rates; each rate's log-sd combines its reported uncertainty with the sensor's error."""
    r_all = np.asarray(rates_kg_h, dtype=float)
    ok = np.isfinite(r_all) & (r_all > 0)
    if not ok.any():
        raise ValueError("no positive rates")
    r = r_all[ok]
    u = np.zeros_like(r) if uncertainty_kg_h is None else np.nan_to_num(np.asarray(uncertainty_kg_h, dtype=float), nan=0.0)[ok]
    s_rep = np.log1p(np.maximum(u, 0.0) / r)                          # reported uncertainty as a log-sd
    s_sens = np.broadcast_to(np.asarray(sensor_log_sigma, dtype=float), r_all.shape)[ok]
    per = np.sqrt(s_rep**2 + s_sens**2)
    if r.size == 1:
        return RateModel(float(np.log(r[0])), float(max(per[0], 0.05)), 1, "single")
    lnr = np.log(r)
    mu = float(lnr.mean())
    sigma = float(np.sqrt(lnr.var(ddof=1) + np.mean(per**2)))
    return RateModel(mu, max(sigma, 0.05), int(r.size), "fitted")


def rate_from_basin(detected_rates_kg_h: np.ndarray, detection_range: tuple[float, float]) -> RateModel:
    """Zero-detection prior: lognormal fitted to detected sources in the basin and segment, truncated to the sensor range."""
    r = np.asarray(detected_rates_kg_h, dtype=float)
    r = r[np.isfinite(r) & (r > 0)]
    lo, hi = detection_range
    if r.size < 5:                                                     # too few: a wide lognormal across the detection range
        mu = 0.5 * (np.log(max(lo, 1e-3)) + np.log(hi)); sigma = (np.log(hi) - np.log(max(lo, 1e-3))) / 4.0
        return RateModel(float(mu), float(sigma), 0, "basin_prior", lo, hi)
    return RateModel(float(np.log(r).mean()), float(max(np.log(r).std(ddof=1), 0.3)), 0, "basin_prior", lo, hi)
