"""Tip-and-cue rule evaluation (TDD section 8.2), run daily over the simulated year.

Triggering sensors (satellites, CMS) have schedules fixed before the year starts, so
their observations are simulated first. Rules are then evaluated day by day on those
logs and schedule cued visits for the target sensor (aircraft, drone) within N days,
respecting a per-facility cooldown and a per-facility cap. The cued sensors are then
simulated with the augmented visit lists. Because cued sensors never trigger rules in
version 1, this two-pass evaluation is equivalent to a single daily loop
(DECISION_LOG 2026-09-30, Phase 7).

The budget_to_widest_interval rule is evaluated monthly: the fast estimator is run on
the observations accumulated so far and the month's budget of visits goes to the
facilities with the widest posterior interval (relative half-width of mass).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from mrvsim.io.seeds import SeedTree
from mrvsim.observe.deployment import CAMPAIGN_LOCAL_HOURS, DeploymentPlan, SensorDeployment
from mrvsim.observe.simulator import ObservationLog, ObservationSet, simulate_observations
from mrvsim.policy.policy import Policy, Rule
from mrvsim.population.generate import Population
from mrvsim.sensors.library import SensorLibrary

HOURS_PER_DAY = 24


@dataclass
class CuedVisits:
    facility: list[int] = field(default_factory=list)
    hour: list[int] = field(default_factory=list)
    rule_kind: list[str] = field(default_factory=list)
    trigger_day: list[int] = field(default_factory=list)

    def add(self, fac: int, hour: int, kind: str, day: int) -> None:
        self.facility.append(int(fac)); self.hour.append(int(hour)); self.rule_kind.append(kind); self.trigger_day.append(int(day))

    def __len__(self) -> int:
        return len(self.facility)


def _visit_hour(rng: np.random.Generator, day: int, lon_deg: float, n_days: int) -> int:
    """A daytime local hour on a random day within [day + 1, day + n_days]."""
    d = day + 1 + rng.integers(0, max(n_days, 1))
    local = rng.choice(CAMPAIGN_LOCAL_HOURS)
    return int(np.clip(round(d * HOURS_PER_DAY + local - lon_deg / 15.0), 0, 8759))


def _apply_cue(cued: CuedVisits, last_cue_day: dict[int, int], n_cued: dict[int, int], rule: Rule, fac: int, day: int,
               rng: np.random.Generator, lon: float) -> None:
    if n_cued.get(fac, 0) >= rule.max_visits_per_facility:
        return
    if fac in last_cue_day and day - last_cue_day[fac] < rule.cooldown_days:
        return
    cued.add(fac, _visit_hour(rng, day, lon, rule.N_days), rule.kind, day)
    last_cue_day[fac] = day; n_cued[fac] = n_cued.get(fac, 0) + 1


def evaluate_trigger_rules(policy: Policy, pop: Population, trig_obs: ObservationSet, seeds: SeedTree) -> dict[str, CuedVisits]:
    """Daily evaluation of satellite_detect and cms_run_length rules on the triggering sensors' logs."""
    out: dict[str, CuedVisits] = {}
    rng = seeds.rng("policy", "cue")
    for ri, rule in enumerate(policy.rules):
        if rule.kind not in ("satellite_detect_to_aircraft", "cms_run_length_to_drone"):
            continue
        cued = out.setdefault(rule.target_sensor, CuedVisits())
        last: dict[int, int] = {}; n: dict[int, int] = {}
        if rule.kind == "satellite_detect_to_aircraft":
            log: ObservationLog = trig_obs.log
            if rule.trigger_sensor not in log.sensor_keys:
                continue
            si = list(log.sensor_keys).index(rule.trigger_sensor)
            m = (log.sensor_idx == si) & log.detected & (log.reported_kg_h >= rule.X_kg_h)
            order = np.argsort(log.hour_idx[m], kind="stable")
            for fac, hour in zip(log.facility_idx[m][order], log.hour_idx[m][order]):
                _apply_cue(cued, last, n, rule, int(fac), int(hour // HOURS_PER_DAY), rng, float(pop.lon[fac]))
        else:
            cms = trig_obs.cms.get(rule.trigger_sensor)
            if cms is None:
                continue
            det = cms.detected & cms.usable
            for fi, fac in enumerate(cms.facilities):
                seq = det[fi].astype(np.int8)
                # run lengths of consecutive detected hours; trigger at the hour the run reaches H
                run = 0
                for h in range(seq.size):
                    run = run + 1 if seq[h] else 0
                    if run == rule.H_hours:
                        _apply_cue(cued, last, n, rule, int(fac), h // HOURS_PER_DAY, rng, float(pop.lon[fac]))
    return out


def merge_cued_visits(plan: DeploymentPlan, cued: dict[str, CuedVisits]) -> DeploymentPlan:
    """Append cued visits to the target sensors' campaign/survey schedules (creating a deployment if absent)."""
    for key, cv in cued.items():
        if len(cv) == 0:
            continue
        dep = plan.deployments.get(key)
        f = np.asarray(cv.facility, np.int64); h = np.asarray(cv.hour, np.int64)
        if dep is None:
            plan.deployments[key] = SensorDeployment(key, np.unique(f), h, f)
        else:
            vh = dep.visit_hours if dep.visit_hours is not None else np.array([], np.int64)
            vf = dep.visit_facility if dep.visit_facility is not None else np.array([], np.int64)
            dep.visit_hours = np.concatenate([vh, h]); dep.visit_facility = np.concatenate([vf, f])
            dep.facilities = np.union1d(dep.facilities, f)
    return plan


def simulate_with_rules(pop: Population, library: SensorLibrary, plan: DeploymentPlan, policy: Policy, seeds: SeedTree, year: int,
                        cache_dir=None) -> tuple[ObservationSet, dict[str, CuedVisits]]:  # noqa: ANN001
    """Two-pass simulation: triggering sensors, rules, then everything (with cued visits merged)."""
    trigger_keys = {r.trigger_sensor for r in policy.rules if r.trigger_sensor}
    if not policy.rules or not trigger_keys:
        return simulate_observations(pop, library, plan, seeds, year, cache_dir=cache_dir, incidental_capture=policy.incidental_capture), {}
    trig_plan = DeploymentPlan(year=year, deployments={k: v for k, v in plan.deployments.items() if k in trigger_keys})
    trig_obs = simulate_observations(pop, library, trig_plan, seeds, year, cache_dir=cache_dir, incidental_capture=policy.incidental_capture)
    cued = evaluate_trigger_rules(policy, pop, trig_obs, seeds)
    full_plan = merge_cued_visits(plan, cued)
    # The same seeds give the triggering sensors identical observations in the full pass (named streams), so the
    # cued visits are consistent with what they reacted to.
    obs = simulate_observations(pop, library, full_plan, seeds, year, cache_dir=cache_dir, incidental_capture=policy.incidental_capture)
    return obs, cued


def allocate_budget_to_widest(policy: Policy, pop: Population, library: SensorLibrary, plan: DeploymentPlan, seeds: SeedTree, year: int,
                              n_draws: int = 1000, cache_dir=None) -> tuple[ObservationSet, CuedVisits]:  # noqa: ANN001
    """budget_to_widest_interval rule: month by month, estimate widths from observations so far and cue the widest.

    Expensive (12 estimator passes); intended for small populations or interactive exploration.
    """
    from mrvsim.estimate import build_inputs, run_fast_estimator

    rules = [r for r in policy.rules if r.kind == "budget_to_widest_interval"]
    cued = CuedVisits()
    if not rules:
        return simulate_observations(pop, library, plan, seeds, year, cache_dir=cache_dir, incidental_capture=policy.incidental_capture), cued
    rule = rules[0]
    rng = seeds.rng("policy", "widest")
    month_start = np.array([0, 744, 1416, 2160, 2880, 3624, 4344, 5088, 5832, 6552, 7296, 8016, 8760])
    n_cued: dict[int, int] = {}
    for m in range(12):
        obs = simulate_observations(pop, library, plan, seeds, year, cache_dir=cache_dir, incidental_capture=policy.incidental_capture)
        # restrict the log to hours before the month start
        log = obs.log
        keep = log.hour_idx < month_start[m]
        for name in log.__dataclass_fields__:
            if name != "sensor_keys":
                setattr(log, name, getattr(log, name)[keep])
        for c in obs.cms.values():
            c.usable[:, month_start[m]:] = False
        inputs = build_inputs(pop, obs, library, seeds)
        post = run_fast_estimator(inputs, pop.strata, pop.priors, library, seeds, n_draws=n_draws, method="is")
        w = post.relative_half_width("mass")
        w[[f for f, c in n_cued.items() if c >= rule.max_visits_per_facility]] = -np.inf
        for fac in np.argsort(-w)[: rule.budget_per_month]:
            if not np.isfinite(w[fac]):
                continue
            day = int(month_start[m] // HOURS_PER_DAY)
            cued.add(int(fac), _visit_hour(rng, day, float(pop.lon[fac]), 28), rule.kind, day)
            n_cued[int(fac)] = n_cued.get(int(fac), 0) + 1
        # merge only this month's new visits into the plan for the next month's pass
        start = len(cued) - min(rule.budget_per_month, len(cued))
        month_cv = CuedVisits(cued.facility[start:], cued.hour[start:], cued.rule_kind[start:], cued.trigger_day[start:])
        plan = merge_cued_visits(plan, {rule.target_sensor: month_cv})
    return simulate_observations(pop, library, plan, seeds, year, cache_dir=cache_dir, incidental_capture=policy.incidental_capture), cued
