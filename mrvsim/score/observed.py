"""Observation-only bounds on annual emissions (PRD section 5.3a, TDD section 7a; added 2026-10-05).

A second view next to the Bayesian estimate, for the question "what do the observations alone establish?".
Nothing about the facility's population enters: no prior on how many sources it has, how large they are or how
often they are on. Only measurements count, and the time nobody observed is bounded by physics alone.

Per facility and year:

- **Observed time** is the hours a continuous monitor was usable (TDD section 5.2). A snapshot is an instant: it
  covers no time, so aircraft and satellite looks do not enter the bounds. Their detections are reported as flags.
- **Lower bound** L: the mass measured in monitored hours with a detection, at the low end of the monitor's
  quantification error. Undetected and unobserved hours contribute nothing.
- **Upper bound** U: the same measured mass at the high end of the quantification error; plus, for monitored
  hours without a detection, the monitor's 90 % detection limit. If any hour of the year was unobserved the upper
  bound is **unbounded**: nothing limits what was emitted then.

Verdict at a bar B: certified iff U <= B, fails iff L > B, otherwise undecided. Certification therefore needs
the whole year under observation; anything less leaves the facility undecided however clean the measured hours.

``cap="throughput"`` is an optional, weaker rule kept for comparison: unobserved hours are bounded by the
facility's reported methane throughput ("it cannot emit more gas than it handles"). It is not the default
because reported gas is marketed gas: vented, flared or unreported associated gas is not in it, and under the
energy-allocated intensity of PRD section 5.1 the cap would certify oil-dominant sites with no observation at all.

Simplifications, stated so they are not mistaken for more than they are: the quantification error is applied as
one common factor exp(+/- z sigma) to all detected hours (fully correlated error, the conservative case); the
detection limit is the rate detected 90 % of the time, so an hour without a detection could still hide a larger
emission one time in ten; the reported throughput in the intensity is taken at face value; false
detections are not removed from the measured mass.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from mrvsim.observe.simulator import ObservationSet
from mrvsim.population.throughput import MIN_GAS_M3_YR
from mrvsim.sensors.library import SensorLibrary

Z_95 = 1.6448536269514722       # two-sided 90 % range of the quantification error, as for the estimate's interval
HOURS = 8760
ROWS = ("observed_hours", "mass_lo_kg_yr", "mass_hi_kg_yr", "intensity_lo", "intensity_hi", "mass_hi_observed_kg_yr",
        "intensity_hi_observed", "snapshot_detections", "snapshot_max_kg_h")


@dataclass
class ObservedBounds:
    """Arrays over facilities; ``table()`` stacks them in the order of ``ROWS`` for persistence."""

    observed_hours: np.ndarray
    mass_lo_kg_yr: np.ndarray
    mass_hi_kg_yr: np.ndarray              # inf unless the whole year was observed (or a cap is chosen)
    intensity_lo: np.ndarray
    intensity_hi: np.ndarray
    mass_hi_observed_kg_yr: np.ndarray     # upper bound on the mass emitted during observed hours only
    intensity_hi_observed: np.ndarray
    snapshot_detections: np.ndarray        # usable snapshot or survey detections (flags, not in the bounds)
    snapshot_max_kg_h: np.ndarray          # largest rate reported by one of them, nan if none

    def table(self) -> np.ndarray:
        return np.stack([np.asarray(getattr(self, r), dtype=float) for r in ROWS])

    def bounds(self, kpi: str) -> tuple[np.ndarray, np.ndarray]:
        return (self.mass_lo_kg_yr, self.mass_hi_kg_yr) if kpi == "mass" else (self.intensity_lo, self.intensity_hi)

    def states(self, kpi: str, bar: float) -> np.ndarray:
        """0 certified (U <= B), 1 fails (L > B), 2 undecided."""
        lo, hi = self.bounds(kpi)
        out = np.full(lo.shape[0], 2, dtype=np.int8)
        out[hi <= bar] = 0
        out[lo > bar] = 1
        return out


def observed_bounds(obs: ObservationSet, library: SensorLibrary, n_fac: int, g_ch4_kg_yr: np.ndarray, f_gas: np.ndarray,
                    gas_m3_yr: np.ndarray, cap: str = "none") -> ObservedBounds:
    """Bounds from the observation logs alone. ``g_ch4_kg_yr`` is the reported methane throughput (the estimator's G-hat)."""
    hours = np.zeros(n_fac); lo = np.zeros(n_fac); hi_obs = np.zeros(n_fac)
    for key, c in obs.cms.items():
        s = library[key]
        sigma = float(s.quantification.sigma)
        limit = float(s.pod.pod90_kg_h)
        det = c.usable & c.detected
        measured = np.where(det, np.nan_to_num(c.reported_kg_h, nan=0.0), 0.0).sum(axis=1)           # kg, hourly series
        np.add.at(hours, c.facilities, c.usable.sum(axis=1))
        np.add.at(lo, c.facilities, measured * np.exp(-Z_95 * sigma))
        np.add.at(hi_obs, c.facilities, measured * np.exp(Z_95 * sigma) + (c.usable & ~c.detected).sum(axis=1) * limit)
    hours = np.minimum(hours, HOURS)                       # two monitors on one facility do not observe more than a year
    if cap not in ("none", "throughput"):
        raise ValueError(f"cap must be 'none' or 'throughput', got {cap!r}")
    has_gas = np.asarray(gas_m3_yr) > MIN_GAS_M3_YR       # a facility reporting no gas has no intensity denominator
    cap_kg_h = np.where(has_gas, np.asarray(g_ch4_kg_yr, float) / HOURS, np.inf) if cap == "throughput" else np.full(n_fac, np.inf)
    with np.errstate(invalid="ignore"):
        unobserved = np.where(hours >= HOURS, 0.0, (HOURS - hours) * cap_kg_h)
    hi = hi_obs + unobserved
    with np.errstate(divide="ignore", invalid="ignore"):
        to_intensity = np.where(has_gas, np.asarray(f_gas, float) / np.asarray(g_ch4_kg_yr, float), np.nan)
        i_lo = np.where(has_gas, lo * to_intensity, 0.0)        # no reported gas: intensity is undefined, so no intensity verdict
        i_hi = np.where(has_gas & np.isfinite(hi), hi * to_intensity, np.inf)
        i_hi_obs = np.where(has_gas, hi_obs * to_intensity, np.inf)

    log = obs.log
    n_det = np.zeros(n_fac); max_r = np.full(n_fac, np.nan)
    if len(log):
        d = log.usable & log.detected
        np.add.at(n_det, log.facility_idx[d], 1)
        r = np.zeros(n_fac); np.maximum.at(r, log.facility_idx[d], np.nan_to_num(log.reported_kg_h[d], nan=0.0))
        max_r = np.where(n_det > 0, r, np.nan)
    return ObservedBounds(hours, lo, hi, i_lo, i_hi, hi_obs, i_hi_obs, n_det, max_r)
