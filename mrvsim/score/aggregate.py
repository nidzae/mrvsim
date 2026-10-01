"""Aggregate replication scores with Monte Carlo standard errors (TDD section 7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from mrvsim.score.metrics import ReplicationScores


@dataclass
class Metric:
    mean: float
    se: float
    n: int
    values: list[float] = field(default_factory=list)

    def as_dict(self) -> dict[str, float]:
        return {"mean": self.mean, "se": self.se, "n": self.n}


def _metric(values: list[float]) -> Metric:
    v = np.asarray([x for x in values if np.isfinite(x)], dtype=float)
    if v.size == 0:
        return Metric(float("nan"), float("nan"), 0, [])
    se = float(v.std(ddof=1) / np.sqrt(v.size)) if v.size > 1 else float("nan")
    return Metric(float(v.mean()), se, int(v.size), v.tolist())


@dataclass
class ScoreReport:
    n_replications: int
    kpi: dict[str, dict[str, Metric]]              # kpi -> metric name -> Metric
    completeness: Metric
    completeness_by_basin: dict[str, Metric]
    cost: dict[str, Metric]
    per_stratum_calibration: dict[str, np.ndarray]  # kpi -> (n_strata,) mean over replications
    state_counts: dict[str, dict[str, Metric]]      # kpi -> state -> share (facilities)
    meta: dict[str, Any] = field(default_factory=dict)

    def headline(self) -> dict[str, Any]:
        out: dict[str, Any] = {"n_replications": self.n_replications, "completeness": self.completeness.as_dict()}
        for kpi, ms in self.kpi.items():
            out[kpi] = {k: m.as_dict() for k, m in ms.items()}
        out["cost"] = {k: m.as_dict() for k, m in self.cost.items()}
        return out

    def calibration_ok(self, lo: float = 0.85, hi: float = 0.95) -> dict[str, bool]:
        return {k: lo <= ms["calibration"].mean <= hi for k, ms in self.kpi.items()}


def aggregate(reps: list[ReplicationScores], n_strata: int, basin_keys: list[str]) -> ScoreReport:
    kpi: dict[str, dict[str, Metric]] = {}
    per_stratum: dict[str, np.ndarray] = {}
    state_counts: dict[str, dict[str, Metric]] = {}
    for k in reps[0].kpi_metrics:
        names = reps[0].kpi_metrics[k].keys()
        kpi[k] = {n: _metric([r.kpi_metrics[k][n] for r in reps]) for n in names if not n.endswith("_se")}
        # single-replication SE where the MC SE is undefined
        if len(reps) == 1:
            kpi[k]["calibration"] = Metric(kpi[k]["calibration"].mean, reps[0].kpi_metrics[k]["calibration_se"], 1)
        per_stratum[k] = np.nanmean(np.stack([r.per_stratum_calibration[k] for r in reps]), axis=0)
        state_counts[k] = {
            "certified": _metric([r.kpi_metrics[k]["certified_share_facilities"] for r in reps]),
            "fails": _metric([r.kpi_metrics[k]["fails_share_facilities"] for r in reps]),
            "indeterminate": _metric([r.kpi_metrics[k]["indeterminate_share_facilities"] for r in reps]),
            # attributes, not states (PRD sections 5.4, 5.4a as amended 2026-10-01)
            "precise": _metric([r.kpi_metrics[k]["precise_share_facilities"] for r in reps]),
            "certified_precise": _metric([r.kpi_metrics[k]["certified_precise_share_facilities"] for r in reps]),
            "certified_prior_only": _metric([r.kpi_metrics[k]["certified_prior_only_share_facilities"] for r in reps]),
        }
    cost_names = reps[0].cost.keys()
    return ScoreReport(
        n_replications=len(reps), kpi=kpi, completeness=_metric([r.completeness for r in reps]),
        completeness_by_basin={b: _metric([r.completeness_by_basin.get(b, float("nan")) for r in reps]) for b in basin_keys},
        cost={n: _metric([r.cost[n] for r in reps]) for n in cost_names}, per_stratum_calibration=per_stratum, state_counts=state_counts,
    )
