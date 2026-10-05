"""Scoring metrics (TDD section 7; PRD sections 5.3-5.5).

All metrics are computed per replication from a :class:`PosteriorSummary` and
the population truth, then aggregated across replications with Monte Carlo
standard errors by :mod:`mrvsim.score.aggregate`. Stratum weights (TDD section
3.1) turn sample fractions into national shares of facilities (W_h^count) and
of throughput (W_h^thr).

Three-state classification at a bar B (PRD sections 5.3-5.4 as amended 2026-10-01;
DECISION_LOG 2026-10-01 "certification is the compliance decision at 95 %"):

* **certified**     K_U,95 = p95 <= B  (the KPI is below the bar with >= 95 % posterior probability)
* **fails**         K_L,5  = p05 > B   (the KPI exceeds the bar with >= 95 % probability; the mirror image)
* **indeterminate** otherwise: the 90 % credible interval straddles the bar

Precision and evidence are reported alongside, not as conditions of certification:

* **precise**       w = (p95 - p05) / (2 p50) <= w_max  (PRD section 5.4, now an attribute)
* **prior-only**    posterior width / prior width > prior_only_ratio, or no usable observation at all
                    (PRD section 5.4a): the certification rests on the population prior, not on data

The earlier rule (certified iff p90 <= B and w <= w_max; fails iff p10 > B) is superseded.
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
    w_max: float             # maximum relative half-width for the "precise" grade (not a certification condition)
    prior_only_ratio: float = 0.9   # posterior/prior width above this = "prior-only" (PRD section 5.4a)

    @classmethod
    def from_config(cls, scoring_cfg: dict[str, Any], kpi: str) -> "Bar":
        w = float(scoring_cfg.get("w_max", 0.30))
        r = float(scoring_cfg.get("prior_only_ratio", 0.9))
        if kpi == "mass":
            return cls("mass", float(scoring_cfg.get("bar_mass_t_yr", 50.0)) * 1000.0, w, r)
        return cls("intensity", float(scoring_cfg.get("bar_intensity", 0.002)), w, r)


def _bounds(post: PosteriorSummary, kpi: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if kpi == "mass":
        return post.mass_kg_yr_p05, post.mass_kg_yr_p10, post.mass_kg_yr_p50, post.mass_kg_yr_p90, post.mass_kg_yr_p95
    return post.intensity_p05, post.intensity_p10, post.intensity_p50, post.intensity_p90, post.intensity_p95


def classify(post: PosteriorSummary, bar: Bar) -> np.ndarray:
    """Per-facility state index into STATES (0 certified, 1 fails, 2 indeterminate); -1 where no posterior.

    Decision only: certified iff p95 <= B, fails iff p05 > B (DECISION_LOG 2026-10-01). Width does not enter.
    """
    p05, p10, p50, p90, p95 = _bounds(post, bar.kpi)
    out = np.full(post.n_fac, 2, dtype=np.int8)
    out[p95 <= bar.B] = 0
    out[p05 > bar.B] = 1
    out[~np.isfinite(p50)] = -1
    return out


def precise(post: PosteriorSummary, bar: Bar) -> np.ndarray:
    """Precision grade: relative half-width w <= w_max (PRD section 5.4 as an attribute)."""
    return post.relative_half_width(bar.kpi) <= bar.w_max


def prior_only(post: PosteriorSummary, bar: Bar) -> np.ndarray:
    """Evidence flag (PRD section 5.4a): the data barely narrowed the prior, or there was no usable observation.

    False where the run has no prior summary (older runs) and no evidence counts.
    """
    ratio = post.evidence_ratio(bar.kpi)
    flag = np.isfinite(ratio) & (ratio > bar.prior_only_ratio)
    if post.evidence is not None:
        flag |= post.evidence.sum(axis=0) == 0
    return flag


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
    precise: dict[str, np.ndarray] = field(default_factory=dict)      # kpi -> per-facility bool (w <= w_max)
    prior_only: dict[str, np.ndarray] = field(default_factory=dict)   # kpi -> per-facility bool (PRD section 5.4a)


def weighted_median(x: np.ndarray, w: np.ndarray) -> float:
    """Median of ``x`` under weights ``w`` (non-finite x ignored); inf if nothing is finite."""
    ok = np.isfinite(x) & (w > 0)
    if not ok.any():
        return float("inf")
    o = np.argsort(x[ok]); xs, ws = x[ok][o], w[ok][o]
    return float(xs[np.searchsorted(np.cumsum(ws) / ws.sum(), 0.5, side="left").clip(0, xs.size - 1)])


def score_replication(post: PosteriorSummary, truth_mass_kg_yr: np.ndarray, truth_intensity: np.ndarray,
                      stratum_idx: np.ndarray, weights_count: np.ndarray, weights_thr: np.ndarray, mmbtu_yr: np.ndarray,
                      bars: dict[str, Bar], completeness: float, completeness_by_basin: dict[str, float], cost: dict[str, float],
                      n_strata: int, scored: np.ndarray | None = None) -> ReplicationScores:
    """TDD section 7 metrics for one replication.

    ``weights_count`` / ``weights_thr`` are per-facility weights (sum to 1 over the sample): the share of the real
    facility population, and of real throughput, that each sampled facility stands for (TDD section 3.1).
    ``scored`` selects the facilities that were estimated (default: those with a finite posterior).

    Since 2026-10-05 (TDD section 7) every share, median and total that describes the *population* is weighted,
    because the sample deliberately over-represents large sites: ``*_share_facilities``, ``width_median`` and
    ``bias_median`` use the count weights; ``*_weighted_throughput`` the throughput weights. ``*_sample`` keeps the
    unweighted sample share. Calibration stays unweighted: it is a property of the estimator on whatever facilities
    it was run on, and the unweighted mean uses every estimated facility equally.
    ``decided_share_emitted_mass`` is the share of true emitted mass at facilities whose state is certified or
    fails (PRD section 5.5a).
    """
    n_fac = int(weights_count.shape[0])
    scored = np.isfinite(post.mass_kg_yr_p50) if scored is None else scored
    kpi_metrics: dict[str, dict[str, float]] = {}
    per_stratum: dict[str, np.ndarray] = {}
    states: dict[str, np.ndarray] = {}
    precise_d: dict[str, np.ndarray] = {}
    prior_only_d: dict[str, np.ndarray] = {}
    wc = weights_count[scored] / weights_count[scored].sum()
    wt = weights_thr[scored] / weights_thr[scored].sum()
    mass_true = truth_mass_kg_yr[scored]; mass_w = float((wc * mass_true).sum())
    for kpi, truth in (("mass", truth_mass_kg_yr), ("intensity", truth_intensity)):
        covered = post.covers(truth, kpi)[scored]
        w = post.relative_half_width(kpi)[scored]
        p50 = _bounds(post, kpi)[2][scored]
        rel_err = (p50 - truth[scored]) / np.where(truth[scored] > 0, truth[scored], np.nan)
        st = classify(post, bars[kpi])
        states[kpi] = st
        s = st[scored]
        pr = precise(post, bars[kpi]); po = prior_only(post, bars[kpi])
        precise_d[kpi] = pr; prior_only_d[kpi] = po
        pr, po = pr[scored], po[scored]
        m = {
            "calibration": float(covered.mean()),
            "calibration_se": float(np.sqrt(covered.mean() * (1 - covered.mean()) / max(covered.size, 1))),
            "width_median": weighted_median(w, wc),
            "width_median_weighted_throughput": weighted_median(w, wt),
            "width_median_sample": float(np.median(w[np.isfinite(w)])) if np.isfinite(w).any() else float("inf"),
            "bias_median": weighted_median(rel_err, wc),
            "certified_share_facilities": float(wc[s == 0].sum()),
            "fails_share_facilities": float(wc[s == 1].sum()),
            "indeterminate_share_facilities": float(wc[s == 2].sum()),
            "certified_share_sample": float((s == 0).mean()),
            "certified_share_weighted_count": float(wc[s == 0].sum()),
            "decided_share_emitted_mass": float((wc * mass_true)[s != 2].sum() / mass_w) if mass_w > 0 else float("nan"),
            "certified_share_weighted_throughput": float(wt[s == 0].sum()),
            "indeterminate_share_weighted_throughput": float(wt[s == 2].sum()),
            "fails_share_weighted_throughput": float(wt[s == 1].sum()),
            # marketed gas energy at certified facilities, on the basis of n_fac facilities in population proportions
            "certified_mmbtu": float(n_fac * (wc * mmbtu_yr[scored])[s == 0].sum()),
            # precision and evidence attributes (PRD sections 5.4, 5.4a as amended 2026-10-01)
            "precise_share_facilities": float(wc[pr].sum()),
            "certified_precise_share_facilities": float(wc[(s == 0) & pr].sum()),
            "certified_precise_share_weighted_throughput": float(wt[(s == 0) & pr].sum()),
            "certified_prior_only_share_facilities": float(wc[(s == 0) & po].sum()),
            "certified_prior_only_share_weighted_throughput": float(wt[(s == 0) & po].sum()),
        }
        kpi_metrics[kpi] = m
        cal_h = np.full(n_strata, np.nan)
        for h in range(n_strata):
            sel = stratum_idx[scored] == h
            if sel.any():
                cal_h[h] = covered[sel].mean()
        per_stratum[kpi] = cal_h
    return ReplicationScores(kpi_metrics, per_stratum, states, completeness, completeness_by_basin, cost, int(scored.sum()),
                             precise=precise_d, prior_only=prior_only_d)


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
