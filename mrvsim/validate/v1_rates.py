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


def simulated_detected_rates(pop, threshold_kg_h: float = 10.0, floors: dict[str, float] | None = None) -> dict[str, np.ndarray]:
    """Facility-total rates seen by an aircraft-style snapshot at random hours, per basin, above the threshold.

    Point-source surveys report facility totals at the time of the overflight; we emulate that with one snapshot
    per facility at a random daytime hour so the sample has the same censoring as the published distributions.
    """
    rng = np.random.default_rng(1)
    hours = rng.integers(0, pop.n_hours, size=pop.n_facilities)
    q = pop.facility_rate_at_pairs(np.arange(pop.n_facilities), hours)
    out: dict[str, np.ndarray] = {}
    for bi, key in enumerate(pop.strata.basins):
        thr = (floors or {}).get(key, threshold_kg_h)
        sel = (pop.basin_idx == bi) & (q > thr)
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
    floors = {b: float(blk["floor_kg_h"]) for b, blk in tg["rate_quantiles_kg_h"].items() if isinstance(blk, dict) and blk.get("floor_kg_h")}
    sims = simulated_detected_rates(pop, floors=floors)
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
        elif sim.size < 30:
            entry.update({"method": "none", "pass": None, "note": f"only {sim.size} simulated sites above the floor {floors.get(basin, 10.0):.1f} kg/h; increase n_per_stratum"})
        else:
            # weaker fallback: every target quantile inside the simulated 90 % bootstrap band of that quantile
            rng = np.random.default_rng(0); ok = True
            for p in ("p25", "p50", "p75", "p90"):
                pct = int(p[1:]); boots = [np.percentile(rng.choice(sim, sim.size), pct) for _ in range(500)] if sim.size else [np.nan]
                lo, hi = np.percentile(boots, [5, 95]); inside = lo <= float(blk[p]) <= hi; ok &= bool(inside)
                entry[f"{p}_band"] = [float(lo), float(hi)]
            entry.update({"method": "quantile_band", "pass": ok}); verdicts.append(ok)
        compared[basin] = entry
    # site-level survival curves (Sherwin 2024 CDFs): simulated snapshot P(site rate >= x) within a factor of 2 where S > 1e-3
    rng = np.random.default_rng(2)
    snap = pop.facility_rate_at_pairs(np.arange(pop.n_facilities), rng.integers(0, pop.n_hours, size=pop.n_facilities))
    for basin, blk in (tg.get("site_survival") or {}).items():
        statuses.append(blk)
        bi = list(pop.strata.basins).index(basin) if basin in pop.strata.basins else None
        if bi is None:
            continue
        sel = pop.basin_idx == bi
        levels = np.array(blk["levels_kg_h"], float); S_t = np.array(blk["fraction_at_or_above"], float)
        S_sim = np.array([(snap[sel] >= x).mean() for x in levels])
        use = S_t * sel.sum() >= 20          # score only levels where the target implies >= 20 simulated facilities
        ratio = (S_sim[use] + 1e-6) / (S_t[use] + 1e-6)
        ok = bool(np.all((ratio > 0.5) & (ratio < 2.0)))
        compared[f"survival_{basin}"] = {"levels_kg_h": levels.tolist(), "S_sim": S_sim.round(5).tolist(), "S_target": S_t.tolist(), "max_ratio_dev": float(np.max(np.abs(np.log(ratio)))), "pass": ok}
        verdicts.append(ok)
    # Cusworth-style persistence f = M/N over sources seen at least once in N = 4 snapshot passes above 10 kg/h
    pers = tg["persistence"]; statuses.append(pers)
    n_pass = 4
    hours = rng.integers(0, pop.n_hours, size=(pop.n_facilities, n_pass))
    det = np.stack([pop.facility_rate_at_pairs(np.arange(pop.n_facilities), hours[:, k]) > 10.0 for k in range(n_pass)], axis=1)
    m = det.sum(axis=1); seen = m > 0
    f_all = float((m[seen] / n_pass).mean()) if seen.any() else float("nan")
    entry = {"sim_persistence_all": f_all, "target": pers.get("value"), "target_ci": pers.get("ci_95"), "by_basin": {}}
    for basin, f_t in (pers.get("by_basin") or {}).items():
        if basin not in pop.strata.basins:
            continue
        sel = (pop.basin_idx == list(pop.strata.basins).index(basin)) & seen
        f_b = float((m[sel] / n_pass).mean()) if sel.any() else float("nan")
        ok = bool(abs(f_b - f_t) <= 0.12) if np.isfinite(f_b) else False
        entry["by_basin"][basin] = {"sim": f_b, "target": f_t, "pass": ok}; verdicts.append(ok)
    entry["pass"] = all(v["pass"] for v in entry["by_basin"].values()) if entry["by_basin"] else None
    compared["persistence"] = entry
    ts = worst_status(statuses)
    if not verdicts:
        res = ValidationResult(VID, NAME, "skipped", tg["pass_rule"], compared, "all targets missing in validation_targets.yaml", tg["citations"], ts)
    else:
        res = ValidationResult(VID, NAME, "pass" if all(verdicts) else "fail", tg["pass_rule"], compared, "", tg["citations"], ts)
    return finish(res, t0, priors)
