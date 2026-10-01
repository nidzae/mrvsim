"""V1: detected-rate distribution above 10 kg/h per basin and persistence (TDD section 9)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats

from mrvsim.io.seeds import SeedTree
from mrvsim.population import generate_population
from mrvsim.validate.common import default_priors, finish, guard_placeholder
from mrvsim.validate.results import ValidationResult
from mrvsim.validate.targets import load_targets, target_missing, worst_status

VID, NAME = "V1", "Detected-rate distribution and persistence per basin"


def simulated_detected_rates(pop, threshold_kg_h: float = 10.0) -> dict[str, np.ndarray]:
    """Facility-total rates seen by an aircraft-style snapshot at random hours, per basin, above the threshold.

    Point-source surveys report facility totals at the time of the overflight; we emulate that with one snapshot
    per facility at a random daytime hour so the sample has the same censoring as the published distributions.
    """
    rng = np.random.default_rng(1)
    hours = rng.integers(0, pop.n_hours, size=pop.n_facilities)
    q = pop.facility_rate_at_pairs(np.arange(pop.n_facilities), hours)
    out: dict[str, np.ndarray] = {}
    for bi, key in enumerate(pop.strata.basins):
        sel = (pop.basin_idx == bi) & (q > threshold_kg_h)
        out[key] = q[sel]
    return out


def run(targets: dict[str, Any] | None = None, n_per_stratum: int = 100, seed: int = 20260930) -> ValidationResult:
    t0 = time.perf_counter()
    tg = (targets or load_targets())["V1"]
    priors = default_priors()
    ref = guard_placeholder(VID, NAME, tg["pass_rule"], tg["citations"], priors)
    if ref:
        return ref
    pop = generate_population({"n_per_stratum": n_per_stratum}, SeedTree(seed), priors=priors)
    sims = simulated_detected_rates(pop)
    compared: dict[str, Any] = {}
    verdicts: list[bool] = []
    statuses = []
    for basin, blk in tg["rate_quantiles_kg_h"].items():
        statuses.append(blk)
        sim = sims.get(basin, np.array([]))
        entry: dict[str, Any] = {"n_sim": int(sim.size), "sim_quantiles": {p: float(np.percentile(sim, int(p[1:]))) if sim.size else None for p in ("p25", "p50", "p75", "p90")}}
        sample_file = (tg.get("rate_sample_files") or {}).get(basin)
        if sample_file and Path(sample_file).exists() and sim.size:
            target_sample = np.loadtxt(sample_file, delimiter=",")
            ks = stats.ks_2samp(sim, target_sample)
            entry.update({"method": "ks_2samp", "p_value": float(ks.pvalue), "pass": bool(ks.pvalue > 0.05)})
            verdicts.append(bool(ks.pvalue > 0.05))
        elif blk.get("status") == "missing" or any(blk.get(p) is None for p in ("p25", "p50", "p75", "p90")):
            entry.update({"method": "none", "pass": None, "note": "target missing"})
        else:
            # weaker fallback: every target quantile inside the simulated 90 % bootstrap band of that quantile
            rng = np.random.default_rng(0); ok = True
            for p in ("p25", "p50", "p75", "p90"):
                pct = int(p[1:]); boots = [np.percentile(rng.choice(sim, sim.size), pct) for _ in range(500)] if sim.size else [np.nan]
                lo, hi = np.percentile(boots, [5, 95]); inside = lo <= float(blk[p]) <= hi; ok &= bool(inside)
                entry[f"{p}_band"] = [float(lo), float(hi)]
            entry.update({"method": "quantile_band", "pass": ok}); verdicts.append(ok)
        compared[basin] = entry
    pers = tg["persistence"]
    inter = pop.z == 1
    compared["persistence"] = {"sim_mean_duty_cycle_intermittent": float(pop.pi[inter].mean()), "sim_share_intermittent": float(inter.mean()),
                               "target": pers.get("value"), "target_ci": pers.get("ci_95"), "pass": None if target_missing(pers) else None}
    if not target_missing(pers):
        lo, hi = pers["ci_95"]; v = float(pop.pi[inter].mean())
        compared["persistence"]["pass"] = bool(lo <= v <= hi); verdicts.append(bool(lo <= v <= hi))
    statuses.append(pers)
    ts = worst_status(statuses)
    if not verdicts:
        res = ValidationResult(VID, NAME, "skipped", tg["pass_rule"], compared, "all targets missing in validation_targets.yaml", tg["citations"], ts)
    else:
        res = ValidationResult(VID, NAME, "pass" if all(verdicts) else "fail", tg["pass_rule"], compared, "", tg["citations"], ts)
    return finish(res, t0, priors)
