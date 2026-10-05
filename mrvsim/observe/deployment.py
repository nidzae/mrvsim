"""Deployment plans: which sensor looks at which facility, when (TDD section 5.1, 8.1).

The policy engine (Phase 7) produces these; Phase 3 ships a direct builder from
the ``policy`` config section so the observation simulator can be exercised:

    policy:
      sensors:
        bridger_gml: {coverage: 0.5, frequency_per_year: 2, targeting: random}
        ghgsat_c:    {coverage: 0.2, frequency_per_year: 12, targeting: throughput}   # tasked satellite
        tropomi:     {coverage: 1.0}                                                # wall-to-wall: every overpass
        cms_generic: {coverage: 0.2, targeting: throughput}

Continuous monitors take ``redundancy`` (independent networks per facility, default 1; cost multiplies).
Targeting: ``random`` | ``throughput`` (top-k facilities by marketed gas; a
cutoff, not weighted sampling) | ``prior_risk`` (top by expected prior mass;
Phase 7) | ``widest_interval`` (adaptive; Phase 7). Coverage is the share of
facilities (``coverage_basis: facilities``, default) or of throughput
(``coverage_basis: throughput``).

Scheduling for campaign/survey sensors (DECISION_LOG 2026-10-01 "Regional
flight campaigns"): ``independent`` (default; each facility's visit days are
drawn on their own) or ``campaign`` (each basin is flown in one window of
``campaign_days`` consecutive days per slice of the year, so neighbouring
facilities are observed together, as real aircraft campaigns do).
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
    redundancy: int = 1                          # continuous monitors: independent networks per facility (TDD section 5.2, 2026-10-05)


@dataclass
class DeploymentPlan:
    year: int
    deployments: dict[str, SensorDeployment] = field(default_factory=dict)

    def sensor_keys(self) -> list[str]:
        return list(self.deployments)


def select_facilities(rng: np.random.Generator, n_fac: int, coverage: float, targeting: str, score: np.ndarray | None,
                      weights: np.ndarray | None = None, coverage_basis: str = "facilities",
                      count_weights: np.ndarray | None = None) -> np.ndarray:
    """Choose covered facilities. ``score`` ranks facilities for non-random targeting.

    ``count_weights`` (per-facility share of the real population, W_h / n_h) makes a targeted coverage share refer
    to the population. Random targeting needs no weights: each facility is covered with the same probability, so
    the covered share of the population equals ``coverage`` in expectation.
    """
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
    elif count_weights is not None:
        # population-weighted coverage (TDD section 8.1 as amended 2026-10-05): the top facilities by score that
        # together stand for `coverage` of the real facility population, not `coverage` of the sample
        cum = np.cumsum(count_weights[order]) / count_weights.sum()
        n = int(np.searchsorted(cum, coverage, side="left") + 1)
    else:
        n = int(round(coverage * n_fac))
    return np.sort(order[:min(n, n_fac)])


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


def campaign_hours_regional(rng: np.random.Generator, facilities: np.ndarray, lon_deg: np.ndarray, basin_idx: np.ndarray,
                            frequency_per_year: int, campaign_days: int = 5, year_hours: int = HOURS_PER_YEAR) -> tuple[np.ndarray, np.ndarray]:
    """Visit hours when a basin is flown as a campaign (TDD section 5.1 as amended 2026-10-01).

    For every basin that has covered facilities and every one of the ``frequency_per_year``
    equal slices of the year, one campaign window of ``campaign_days`` consecutive days starts
    at a uniform day inside the slice (clipped so the window fits; a window longer than the
    slice becomes the slice). Each covered facility in that basin is visited once per window,
    on a uniform day within it, at a daytime local hour. Same return shape as ``campaign_hours``.
    """
    if frequency_per_year <= 0 or facilities.size == 0:
        return np.array([], dtype=np.int64), np.array([], dtype=np.int64)
    f = int(frequency_per_year); D = max(int(campaign_days), 1)
    slice_days = 365.0 / f
    fac_basin = basin_idx[facilities]
    hours: list[np.ndarray] = []; facs: list[np.ndarray] = []
    for b in np.unique(fac_basin):                       # basins in index order: reproducible for a given seed
        members = facilities[fac_basin == b]
        n_f = members.size
        span = min(D, int(np.floor(slice_days)))          # window length in whole days, never longer than the slice
        start = np.arange(f) * slice_days + rng.random(f) * max(slice_days - span, 0.0)      # (f,) window start day
        offset = rng.integers(0, span, size=(n_f, f))                                           # day within the window
        day = np.floor(start)[None, :] + offset
        local_h = rng.choice(CAMPAIGN_LOCAL_HOURS, size=(n_f, f))
        utc_offset_h = -lon_deg[members][:, None] / 15.0
        hour = np.clip(np.round(day * 24 + local_h + utc_offset_h), 0, year_hours - 1).astype(np.int64)
        hours.append(hour.ravel()); facs.append(np.repeat(members, f))
    return np.concatenate(hours), np.concatenate(facs)


def build_plan(policy_cfg: Mapping[str, Any], seeds: SeedTree, n_fac: int, lon_deg: np.ndarray,
               throughput_score: np.ndarray, sensor_modes: Mapping[str, tuple[str, str, bool]], year: int,
               basin_idx: np.ndarray | None = None, count_weights: np.ndarray | None = None,
               throughput_weights: np.ndarray | None = None) -> DeploymentPlan:
    """Build a plan from the ``policy`` config section.

    ``sensor_modes`` maps sensor key -> (schedule, observation_mode, tasked) from the sensor library.
    ``basin_idx`` (per facility) enables ``scheduling: campaign``; without it every sensor uses independent dates.
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
        # `coverage_weighting: population` (set by the pipeline for runs since 2026-10-05): coverage shares refer to the
        # real population through the stratum weights; absent (older runs) they refer to the sample.
        weighted = str(policy_cfg.get("coverage_weighting", "sample")) == "population" and count_weights is not None
        facs = select_facilities(rng, n_fac, cov, targeting, score,
                                 weights=throughput_weights if weighted and throughput_weights is not None else throughput_score,
                                 coverage_basis=basis, count_weights=count_weights if weighted else None)
        dep = SensorDeployment(sensor_key=key, facilities=facs, redundancy=max(int(spec.get("redundancy", 1)), 1) if schedule == "hourly" else 1)
        if schedule in ("campaign", "survey"):
            freq = int(spec.get("frequency_per_year", 1))
            scheduling = str(spec.get("scheduling", "independent"))
            if scheduling == "campaign" and basin_idx is not None:
                dep.visit_hours, dep.visit_facility = campaign_hours_regional(seeds.rng("policy", "dates", sensor=key), facs, lon_deg, basin_idx, freq,
                                                                              int(spec.get("campaign_days", 5)))
            else:
                dep.visit_hours, dep.visit_facility = campaign_hours(seeds.rng("policy", "dates", sensor=key), facs, lon_deg, freq)
        elif schedule == "orbit" and tasked:
            dep.taskings_per_year = int(spec.get("frequency_per_year", 12))
        plan.deployments[key] = dep
    return plan
