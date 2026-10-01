"""Scoring metrics (TDD section 7; PRD sections 5.3-5.5).

All metrics are computed per replication from a :class:`PosteriorSummary` and
the population truth, then aggregated across replications with Monte Carlo
standard errors by :mod:`mrvsim.score.aggregate`. Stratum weights (TDD section
3.1) turn sample fractions into national shares of facilities (W_h^count) and
of throughput (W_h^thr).

Three-state classification at a bar (B, w_max) (PRD sections 5.3-5.4, QUICKSTART):

* **certified**     K_U,90 = p90 <= B and w <= w_max
* **fails**         the one-sided 90 % lower bound p10 > B (the KPI exceeds the bar with >= 90 % probability)
* **indeterminate** otherwise (interval straddles B, or too wide)

The one-sided bounds are used for both decisions so that "certified" and
"fails" are symmetric statements at the same confidence (DECISION_LOG 2026-09-30,
Phase 5); the two-sided [p5, p95] interval defines the width w.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from mrvsim.estimate.fast import PosteriorSummary

STATES = ("certified", "fails", "indeterminate")


@dataclass
class Bar:
    kpi: str                 # "mass" (t/yr) or "intensity" (fraction)
    B: float                 # bar value in KPI units (mass: kg/yr internally; callers pass t/yr via from_config)
    w_max: float             # maximum relative half-width

    @classmethod
    def from_config(cls, scoring_cfg: dict[str, Any], kpi: str) -> "Bar":
        w = float(scoring_cfg.get("w_max", 0.30))
        if kpi == "mass":
            return cls("mass", float(scoring_cfg.get("bar_mass_t_yr", 50.0)) * 1000.0, w)
        return cls("intensity", float(scoring_cfg.get("bar_intensity", 0.002)), w)


def _bounds(post: PosteriorSummary, kpi: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if kpi == "mass":
        return post.mass_kg_yr_p05, post.mass_kg_yr_p10, post.mass_kg_yr_p50, post.mass_kg_yr_p90, post.mass_kg_yr_p95
    return post.intensity_p05, post.intensity_p10, post.intensity_p50, post.intensity_p90, post.intensity_p95


def classify(post: PosteriorSummary, bar: Bar) -> np.ndarray:
    """Per-facility state index into STATES (0 certified, 1 fails, 2 indeterminate); -1 where no posterior."""
    p05, p10, p50, p90, p95 = _bounds(post, bar.kpi)
    w = post.relative_half_width(bar.kpi)
    out = np.full(post.n_fac, 2, dtype=np.int8)
    out[(p90 <= bar.B) & (w <= bar.w_max)] = 0
    out[p10 > bar.B] = 1
    out[~np.isfinite(p50)] = -1
    return out


@dataclass
class ReplicationScores:
    """Metrics for one replication, with per-stratum breakdowns where defined."""

    kpi_metrics: dict[str, dict[str, float]]          # kpi -> {calibration, width, bias, certified_share_fac, ...}
    per_stratum_calibration: dict[str, np.ndarray]    # kpi -> (n_strata,) calibration
    states: dict[str, np.ndarray]                     # kpi -> per-facility state index
    completeness: float
    completeness_by_basin: dict[str, float]
    cost: dict[str, float]
    n_scored: int
    extras: dict[str, Any] = field(default_factory=dict)


def score_replication(post: PosteriorSummary, truth_mass_kg_yr: np.ndarray, truth_intensity: np.ndarray,
                      stratum_idx: np.ndarray, weights_count: np.ndarray, weights_thr: np.ndarray, mmbtu_yr: np.ndarray,
                      bars: dict[str, Bar], completeness: float, completeness_by_basin: dict[str, float], cost: dict[str, float],
                      n_strata: int, scored: np.ndarray | None = None) -> ReplicationScores:
    """TDD section 7 metrics for one replication.

    ``weights_count`` / ``weights_thr`` are per-facility weights W_h / n_h (sum to 1 over the sample);
    ``scored`` selects the facilities that were estimated (default: those with a finite posterior).
    """
    scored = np.isfinite(post.mass_kg_yr_p50) if scored is None else scored
    kpi_metrics: dict[str, dict[str, float]] = {}
    per_stratum: dict[str, np.ndarray] = {}
    states: dict[str, np.ndarray] = {}
    wc = weights_count[scored] / weights_count[scored].sum()
    wt = weights_thr[scored] / weights_thr[scored].sum()
    for kpi, truth in (("mass", truth_mass_kg_yr), ("intensity", truth_intensity)):
        covered = post.covers(truth, kpi)[scored]
        w = post.relative_half_width(kpi)[scored]
        p50 = _bounds(post, kpi)[2][scored]
        rel_err = (p50 - truth[scored]) / np.where(truth[scored] > 0, truth[scored], np.nan)
        st = classify(post, bars[kpi])
        states[kpi] = st
        s = st[scored]
        m = {
            "calibration": float(covered.mean()),
            "calibration_se": float(np.sqrt(covered.mean() * (1 - covered.mean()) / max(covered.size, 1))),
            "width_median": float(np.median(w[np.isfinite(w)])) if np.isfinite(w).any() else float("inf"),
            "bias_median": float(np.nanmedian(rel_err)),
            "certified_share_facilities": float((s == 0).mean()),
            "fails_share_facilities": float((s == 1).mean()),
            "indeterminate_share_facilities": float((s == 2).mean()),
            "certified_share_weighted_count": float(wc[s == 0].sum()),
            "certified_share_weighted_throughput": float(wt[s == 0].sum()),
            "indeterminate_share_weighted_throughput": float(wt[s == 2].sum()),
            "fails_share_weighted_throughput": float(wt[s == 1].sum()),
            "certified_mmbtu": float(mmbtu_yr[scored][s == 0].sum()),
        }
        kpi_metrics[kpi] = m
        cal_h = np.full(n_strata, np.nan)
        for h in range(n_strata):
            sel = stratum_idx[scored] == h
            if sel.any():
                cal_h[h] = covered[sel].mean()
        per_stratum[kpi] = cal_h
    return ReplicationScores(kpi_metrics, per_stratum, states, completeness, completeness_by_basin, cost, int(scored.sum()))


# ---------------------------------------------------------------------------- completeness (PRD section 5.5)

def completeness_from_detection_probability(q_kg_h: np.ndarray, on_hours: np.ndarray, p_detect_once: np.ndarray,
                                            src_facility: np.ndarray, basin_of_facility: np.ndarray, basin_keys: list[str],
                                            threshold_kg_h: float = 10.0) -> tuple[float, dict[str, float]]:
    """C = sum_j q_j H_j 1[q_j > 10] P_bar_j / sum_j q_j H_j 1[q_j > 10] [jacob2022], overall and by basin."""
    big = q_kg_h > threshold_kg_h
    mass = q_kg_h * on_hours
    num = (mass * p_detect_once)[big]; den = mass[big]
    overall = float(num.sum() / den.sum()) if den.sum() > 0 else float("nan")
    by_basin: dict[str, float] = {}
    b_src = basin_of_facility[src_facility]
    for bi, key in enumerate(basin_keys):
        sel = big & (b_src == bi)
        d = mass[sel].sum()
        by_basin[key] = float((mass * p_detect_once)[sel].sum() / d) if d > 0 else float("nan")
    return overall, by_basin
