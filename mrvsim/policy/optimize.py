"""Optimizer (TDD section 8.3) [optuna]: minimise cost subject to calibration, width, and certified-share constraints.

Each trial samples a policy from the search space, scores it with the pipeline at a
reduced number of replications (default 50 in the TDD; tests use fewer), and reports
(cost, width) as a two-objective minimisation with constraints kappa >= kappa_min and
certified throughput share >= theta. Optuna's NSGA-II sampler handles the categorical
switches and the constraints; the non-dominated feasible trials form the Pareto set,
and the top candidates are re-scored at full R.

Reinforcement learning is deliberately not used (TDD section 8.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from mrvsim.io.config import RunConfig
from mrvsim.policy.policy import Policy, SensorPolicy
from mrvsim.sensors.library import SensorLibrary

OPTIMISABLE_SENSORS = ("bridger_gml", "kairos", "aviris_ng_gao", "ghgsat_c", "cms_generic", "drone_generic", "ogi")


@dataclass
class SearchSpace:
    """Which policy parameters the optimizer may vary (TDD section 8.1 ranges)."""

    sensors: tuple[str, ...] = ("bridger_gml", "ghgsat_c", "cms_generic")
    coverage_range: tuple[float, float] = (0.0, 1.0)
    frequency_range: tuple[int, int] = (0, 12)
    targeting_choices: tuple[str, ...] = ("random", "throughput")
    allow_disable: bool = True
    fixed: dict[str, SensorPolicy] = field(default_factory=dict)       # sensors always on with fixed settings (e.g. TROPOMI)


@dataclass
class TrialResult:
    number: int
    policy: Policy
    cost_usd: float
    width: float
    calibration: float
    certified_share_throughput: float
    feasible: bool
    rescored: bool = False
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParetoResult:
    trials: list[TrialResult]
    pareto: list[TrialResult]
    rescored_top: list[TrialResult]
    constraints: dict[str, float]
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        def tr(t: TrialResult) -> dict[str, Any]:
            return {"number": t.number, "policy": t.policy.to_dict(), "cost_usd": t.cost_usd, "width": t.width, "calibration": t.calibration,
                    "certified_share_throughput": t.certified_share_throughput, "feasible": t.feasible, "rescored": t.rescored}
        return {"constraints": self.constraints, "meta": self.meta, "pareto": [tr(t) for t in self.pareto],
                "rescored_top": [tr(t) for t in self.rescored_top], "all_trials": [tr(t) for t in self.trials]}


def policy_from_trial(trial, space: SearchSpace, name: str) -> Policy:  # noqa: ANN001
    sensors: dict[str, SensorPolicy] = dict(space.fixed)
    for key in space.sensors:
        enabled = trial.suggest_categorical(f"{key}.enabled", [True, False]) if space.allow_disable else True
        cov = trial.suggest_float(f"{key}.coverage", *space.coverage_range)
        freq = trial.suggest_int(f"{key}.frequency", *space.frequency_range)
        tgt = trial.suggest_categorical(f"{key}.targeting", list(space.targeting_choices))
        sensors[key] = SensorPolicy(enabled=enabled and cov > 0 and (freq > 0 or key.startswith("cms")), coverage=cov,
                                    frequency_per_year=max(freq, 1 if key.startswith("cms") else freq), targeting=tgt)
    p = Policy(sensors=sensors, name=name)
    p.validate()
    return p


def score_policy(cfg: RunConfig, policy: Policy, replications: int, n_draws: int, facilities_per_stratum: int | None,
                 library: SensorLibrary, root: str | Path, kpi: str = "intensity") -> dict[str, float]:
    from mrvsim.pipeline import run_scored

    cfg_p = cfg.with_overrides(policy=policy.to_policy_cfg(), name=f"opt-{policy.name}", replications=replications)
    rep, _, _ = run_scored(cfg_p, root=root, replications=replications, n_draws=n_draws, facilities_per_stratum=facilities_per_stratum,
                           library=library, keep_last=False)
    k = rep.kpi[kpi]
    return {"cost_usd": rep.cost["cost_total_usd"].mean, "width": k["width_median"].mean, "calibration": k["calibration"].mean,
            "certified_share_throughput": k["certified_share_weighted_throughput"].mean}


def pareto_front(trials: list[TrialResult]) -> list[TrialResult]:
    feas = [t for t in trials if t.feasible and np.isfinite(t.cost_usd) and np.isfinite(t.width)]
    front = []
    for t in feas:
        dominated = any((o.cost_usd <= t.cost_usd and o.width <= t.width) and (o.cost_usd < t.cost_usd or o.width < t.width) for o in feas)
        if not dominated:
            front.append(t)
    return sorted(front, key=lambda t: t.cost_usd)


def optimize(cfg: RunConfig, library: SensorLibrary, space: SearchSpace, n_trials: int = 30, kappa_min: float = 0.85, w_max: float = 0.30,
             theta: float = 0.5, replications_trial: int = 50, replications_full: int | None = None, n_draws: int = 2000,
             facilities_per_stratum: int | None = 10, top_k: int = 10, seed: int = 0, root: str | Path = "runs", kpi: str = "intensity") -> ParetoResult:
    """Optuna NSGA-II over (cost, width) with constraints; re-score the top_k feasible trials at full R (TDD section 8.3)."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    results: dict[int, TrialResult] = {}

    def objective(trial) -> tuple[float, float]:  # noqa: ANN001
        pol = policy_from_trial(trial, space, f"t{trial.number}")
        sc = score_policy(cfg, pol, replications_trial, n_draws, facilities_per_stratum, library, root, kpi)
        c = (kappa_min - sc["calibration"], theta - sc["certified_share_throughput"])   # <= 0 means satisfied
        if hasattr(trial, "set_constraint"):          # optuna >= 5: named constraints, <= 0 feasible
            trial.set_constraint("kappa_deficit", float(c[0]))
            trial.set_constraint("share_deficit", float(c[1]))
        else:                                         # optuna 3/4
            trial.set_user_attr("constraints", c)
        feasible = c[0] <= 0 and c[1] <= 0 and sc["width"] <= w_max
        results[trial.number] = TrialResult(trial.number, pol, sc["cost_usd"], sc["width"], sc["calibration"], sc["certified_share_throughput"], feasible)
        return sc["cost_usd"], sc["width"]

    pop_size = max(8, min(20, n_trials // 2))
    try:
        sampler = optuna.samplers.NSGAIISampler(seed=seed, population_size=pop_size)   # constraints via trial.set_constraint (optuna >= 5)
        if not hasattr(optuna.trial.Trial, "set_constraint"):
            raise AttributeError
    except AttributeError:
        sampler = optuna.samplers.NSGAIISampler(seed=seed, population_size=pop_size,
                                                constraints_func=lambda t: tuple(t.user_attrs.get("constraints", (1.0, 1.0))))
    study = optuna.create_study(directions=["minimize", "minimize"], sampler=sampler)
    study.optimize(objective, n_trials=n_trials)
    trials = [results[k] for k in sorted(results)]
    front = pareto_front(trials)
    rescored: list[TrialResult] = []
    R_full = replications_full or cfg.replications
    for t in front[:top_k]:
        sc = score_policy(cfg, t.policy, R_full, n_draws, facilities_per_stratum, library, root, kpi)
        rescored.append(TrialResult(t.number, t.policy, sc["cost_usd"], sc["width"], sc["calibration"], sc["certified_share_throughput"],
                                    sc["calibration"] >= kappa_min and sc["certified_share_throughput"] >= theta and sc["width"] <= w_max, rescored=True))
    return ParetoResult(trials, front, rescored, {"kappa_min": kappa_min, "w_max": w_max, "theta": theta},
                        {"n_trials": n_trials, "replications_trial": replications_trial, "replications_full": R_full, "n_draws": n_draws,
                         "facilities_per_stratum": facilities_per_stratum, "kpi": kpi, "sampler": "NSGAII", "seed": seed})
