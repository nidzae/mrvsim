"""Cost model (TDD section 7, PRD section 5.6). Reads per-sensor costs from the sensor YAML [cost-assumptions].

Accounting conventions (DECISION_LOG 2026-09-30, Phase 5):
* campaign / survey sensors: per_site_visit_usd x visits actually scheduled (weather losses are still paid for);
* tasked satellites: per_tasking_usd x overpasses tasked (usable or not; incidental scene members are free);
* wall-to-wall satellites and CMS: per_site_year_usd x facilities covered;
* sample costs are scaled to national totals with the facility-count stratum weights W_h / n_h,
  i.e. ``national_cost = sum_i w_i * cost_i * N_national`` where N_national is supplied by the caller
  (default: the sample itself, so costs are per-sample unless a national facility count is given).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from mrvsim.observe.deployment import DeploymentPlan
from mrvsim.observe.simulator import ObservationSet
from mrvsim.sensors.library import SensorLibrary


@dataclass
class CostBreakdown:
    by_sensor_usd: dict[str, float]
    total_usd: float
    per_facility_usd: np.ndarray        # (n_fac,) cost attributed to each sampled facility (unweighted)

    def as_dict(self) -> dict[str, float]:
        return {**{f"cost_{k}_usd": v for k, v in self.by_sensor_usd.items()}, "cost_total_usd": self.total_usd}


def deployment_cost(plan: DeploymentPlan, obs: ObservationSet, library: SensorLibrary, n_fac: int,
                    count_weights: np.ndarray | None = None) -> CostBreakdown:
    """Monitoring cost by sensor and in total.

    Without ``count_weights``: the plain sum over the sampled facilities. With them (per-facility share of the real
    population, summing to 1): n_fac x the population-mean cost per facility, i.e. what the policy would cost on
    n_fac facilities drawn in the population's true proportions (TDD section 7 as amended 2026-10-05). The two agree
    when the sample is self-weighting.
    """
    per_fac = np.zeros(n_fac)
    by_sensor: dict[str, float] = {}
    for key, dep in plan.deployments.items():
        s = library[key]; c = s.cost
        cost_fac = np.zeros(n_fac)
        if s.schedule in ("campaign", "survey"):
            if dep.visit_facility is not None and c.per_site_visit_usd is not None:
                np.add.at(cost_fac, dep.visit_facility, c.per_site_visit_usd)
        elif s.schedule == "orbit" and s.orbit is not None and s.orbit.tasked:
            if c.per_tasking_usd is not None:
                si = list(obs.log.sensor_keys).index(key) if key in obs.log.sensor_keys else -1
                if si >= 0:
                    rows = (obs.log.sensor_idx == si) & ~obs.log.incidental     # incidental scene members are not taskings
                    np.add.at(cost_fac, obs.log.facility_idx[rows], c.per_tasking_usd)
        else:  # wall-to-wall satellites, CMS
            if c.per_site_year_usd is not None:
                cost_fac[dep.facilities] += c.per_site_year_usd
        by_sensor[key] = float(cost_fac.sum()) if count_weights is None else float(n_fac * (count_weights * cost_fac).sum())
        per_fac += cost_fac
    total = float(per_fac.sum()) if count_weights is None else float(n_fac * (count_weights * per_fac).sum())
    return CostBreakdown(by_sensor, total, per_fac)


def cost_metrics(cost: CostBreakdown, detected_mass_kg: float, certified_mmbtu: float) -> dict[str, float]:
    """Cost per tonne detected and per certified MMBtu (PRD section 5.6)."""
    return {
        "cost_total_usd": cost.total_usd,
        "cost_per_tonne_detected_usd": cost.total_usd / (detected_mass_kg / 1000.0) if detected_mass_kg > 0 else float("inf"),
        "cost_per_certified_mmbtu_usd": cost.total_usd / certified_mmbtu if certified_mmbtu > 0 else float("inf"),
    }


def detected_mass_kg(pop_q_kg_h: np.ndarray, on_hours: np.ndarray, p_detect_once: np.ndarray, detected_sources: np.ndarray | None = None,
                     source_weights: np.ndarray | None = None) -> float:
    """sum_j detected mass_j: mass of sources detected at least once this year (realised); falls back to expected if no flags.

    ``source_weights`` (n_fac x the count weight of each source's facility) puts the sum on the same
    population-proportional basis as :func:`deployment_cost`.
    """
    mass = pop_q_kg_h * on_hours
    if source_weights is not None:
        mass = mass * source_weights
    if detected_sources is not None:
        return float(mass[detected_sources].sum())
    return float((mass * p_detect_once).sum())
