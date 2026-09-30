"""Deployment plans: which sensor looks at which facility, when (TDD section 5.1, 8.1).

The policy engine (Phase 7) produces these; Phase 3 ships a direct builder from
the ``policy`` config section so the observation simulator can be exercised:

    policy:
      sensors:
        bridger_gml: {coverage: 0.5, frequency_per_year: 2, targeting: random}
        ghgsat_c:    {coverage: 0.2, frequency_per_year: 12, targeting: throughput}   # tasked satellite
        tropomi:     {coverage: 1.0}                                                # wall-to-wall: every overpass
        cms_generic: {coverage: 0.2, targeting: throughput}

Targeting: ``random`` | ``throughput`` (top facilities by marketed gas) |
``prior_risk`` (top by expected prior mass; Phase 7) | ``widest_interval``
(adaptive; Phase 7). Coverage is the share of facilities (``coverage_basis:
facilities``, default) or of throughput (``coverage_basis: throughput``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

import numpy as np

from mrvsim.io.seeds import SeedTree

HOURS_PER_YEAR = 8760
# Campaign/survey visits happen in daytime working hours; local solar hour -> UTC via longitude.
CAMPAIGN_LOCAL_HOURS = (10, 11, 12, 13, 14, 15)


@dataclass
class SensorDeployment:
    sensor_key: str
    facilities: np.ndarray                       # facility indices covered
    visit_hours: np.ndarray | None = None        # (n_visits,) simulation hours for campaign/survey sensors
    visit_facility: np.ndarray | None = None     # (n_visits,) facility index per visit
    taskings_per_year: int | None = None         # tasked satellites: number of overpasses to use per facility


@dataclass
class DeploymentPlan:
    year: int
    deployments: dict[str, SensorDeployment] = field(default_factory=dict)

    def sensor_keys(self) -> list[str]:
        return list(self.deployments)


def select_facilities(rng: np.random.Generator, n_fac: int, coverage: float, targeting: str, score: np.ndarray | None,
                      weights: np.ndarray | None = None, coverage_basis: str = "facilities") -> np.ndarray:
    """Choose covered facilities. ``score`` ranks facilities for non-random targeting."""
    coverage = float(np.clip(coverage, 0.0, 1.0))
    if coverage == 0.0:
        return np.array([], dtype=np.int64)
    if targeting == "random" or score is None:
        n = int(round(coverage * n_fac))
        return np.sort(rng.choice(n_fac, size=n, replace=False))
    order = np.argsort(-score, kind="stable")
    if coverage_basis == "throughput" and weights is not None:
        cum = np.cumsum(weights[order]) / weights.sum()
        n = int(np.searchsorted(cum, coverage, side="left") + 1)
    else:
        n = int(round(coverage * n_fac))
    return np.sort(order[:n])


def campaign_hours(rng: np.random.Generator, facilities: np.ndarray, lon_deg: np.ndarray, frequency_per_year: int,
                   year_hours: int = HOURS_PER_YEAR) -> tuple[np.ndarray, np.ndarray]:
    """Visit hours for each covered facility: ``frequency_per_year`` random days, daytime local hour.

    Visits are spread over the year by stratified day sampling (one visit per
    equal slice of the year) so two visits are not usually in the same week.
    """
    if frequency_per_year <= 0 or facilities.size == 0:
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64)
    n_f = facilities.size
    f = frequency_per_year
    slice_days = 365.0 / f
    day = (np.arange(f)[None, :] + rng.random((n_f, f))) * slice_days           # (n_f, f) fractional day of year
    local_h = rng.choice(CAMPAIGN_LOCAL_HOURS, size=(n_f, f))
    utc_offset_h = -lon_deg[facilities][:, None] / 15.0                          # west longitude -> later UTC
    hour = np.floor(day) * 24 + local_h + utc_offset_h
    hour = np.clip(np.round(hour), 0, year_hours - 1).astype(np.int64)
    return hour.ravel(), np.repeat(facilities, f)


def build_plan(policy_cfg: Mapping[str, Any], seeds: SeedTree, n_fac: int, lon_deg: np.ndarray,
               throughput_score: np.ndarray, sensor_modes: Mapping[str, tuple[str, str, bool]], year: int) -> DeploymentPlan:
    """Build a plan from the ``policy`` config section.

    ``sensor_modes`` maps sensor key -> (schedule, observation_mode, tasked) from the sensor library.
    """
    plan = DeploymentPlan(year=year)
    for key, spec in (policy_cfg.get("sensors") or {}).items():
        if key not in sensor_modes:
            raise KeyError(f"policy references unknown sensor {key!r}")
        schedule, mode, tasked = sensor_modes[key]
        if not spec.get("enabled", True):
            continue
        cov = float(spec.get("coverage", 1.0))
        targeting = str(spec.get("targeting", "random"))
        basis = str(spec.get("coverage_basis", "facilities"))
        rng = seeds.rng("policy", "select", sensor=key)
        score = throughput_score if targeting in ("throughput", "prior_risk", "widest_interval") else None
        facs = select_facilities(rng, n_fac, cov, targeting, score, weights=throughput_score, coverage_basis=basis)
        dep = SensorDeployment(sensor_key=key, facilities=facs)
        if schedule in ("campaign", "survey"):
            freq = int(spec.get("frequency_per_year", 1))
            dep.visit_hours, dep.visit_facility = campaign_hours(seeds.rng("policy", "dates", sensor=key), facs, lon_deg, freq)
        elif schedule == "orbit" and tasked:
            dep.taskings_per_year = int(spec.get("frequency_per_year", 12))
        plan.deployments[key] = dep
    return plan
