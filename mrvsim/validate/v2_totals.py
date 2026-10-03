"""V2: simulated basin totals vs Sherwin 2024 and a FEAST 3.1 cross-check (TDD section 9)."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from mrvsim.io.seeds import SeedTree
from mrvsim.population import generate_population
from mrvsim.validate.common import basin_loss_rate, default_priors, finish, guard_placeholder, weighted_basin_total_t_h
from mrvsim.validate.results import ValidationResult
from mrvsim.validate.targets import load_targets, target_missing, worst_status

VID, NAME = "V2", "Basin totals vs published and FEAST 3.1 cross-check"


def run(targets: dict[str, Any] | None = None, n_per_stratum: int = 100, seed: int = 20260930, feast_facilities: int = 400) -> ValidationResult:
    t0 = time.perf_counter()
    tg = (targets or load_targets())["V2"]
    priors = default_priors()
    ref = guard_placeholder(VID, NAME, tg["pass_rule"], tg["citations"], priors)
    if ref:
        return ref
    pop = generate_population({"n_per_stratum": n_per_stratum}, SeedTree(seed), priors=priors)
    truth = pop.true_mass_kg_yr()
    compared: dict[str, Any] = {}; verdicts = []; statuses = []
    # basin loss rates: sample emissions over sample marketed methane (stratum-weighted), vs Sherwin 2024 Table S10
    for basin, blk in (tg.get("loss_rates_by_basin") or {}).items():
        statuses.append(blk)
        if basin not in pop.strata.basins:
            continue
        # Corrected 2026-10-03: the previous ratio used throughput weights on both sums, which weights emissions by gas.
        lr, how = basin_loss_rate(pop, truth, basin)
        lo, hi = blk["ci_95"]; ok = bool(lo <= lr <= hi)
        compared[f"loss_rate_{basin}"] = {"sim_true_loss_rate": lr, "published": blk["value"], "published_ci": blk["ci_95"], "campaign": blk.get("campaign"), "pass": ok,
                                          "estimator": how, "note": "published value is production + midstream; the simulated value covers well pads only when the site table is used"}
        verdicts.append(ok)
    for basin, blk in tg["basin_totals_t_h"].items():
        statuses.append(blk)
        sim = weighted_basin_total_t_h(pop, truth, basin)
        entry = {"sim_sample_total_t_h": sim, "published": blk.get("value"), "published_ci": blk.get("ci_95"),
                 "note": "sample total; national scaling needs a facility count per stratum"}
        if target_missing(blk):
            entry["pass"] = None
        else:
            lo, hi = blk["ci_95"]; ok = lo <= sim <= hi; entry["pass"] = bool(ok); verdicts.append(bool(ok))
        compared[basin] = entry
    # FEAST cross-check on a facility subsample (FEAST is slow for thousands of component types)
    from mrvsim.validate.feast_adapter import feast_annual_mass_kg, feast_available
    tol = float(tg.get("feast_tolerance", 0.15))
    if feast_available():
        rng = np.random.default_rng(seed)
        sub = np.sort(rng.choice(pop.n_facilities, size=min(feast_facilities, pop.n_facilities), replace=False))
        fe = feast_annual_mass_kg(pop, sub, seed=seed)
        ratio = fe["ratio_feast_over_expected"]
        ok = bool(abs(ratio - 1.0) <= tol)
        compared["feast_cross_check"] = {**fe, "tolerance": tol, "pass": ok}
        verdicts.append(ok)
    else:
        compared["feast_cross_check"] = {"pass": None, "note": "FEAST 3.1 not available (vendor/feast submodule missing)"}
    ts = worst_status(statuses)
    if not verdicts:
        res = ValidationResult(VID, NAME, "skipped", tg["pass_rule"], compared, "all published targets missing and FEAST unavailable", tg["citations"], ts)
    else:
        missing = [b for b, e in compared.items() if e.get("pass") is None and b != "feast_cross_check"]
        reason = f"published basin totals missing for: {', '.join(missing)}" if missing else ""
        res = ValidationResult(VID, NAME, "pass" if all(verdicts) else "fail", tg["pass_rule"], compared, reason, tg["citations"], ts)
    return finish(res, t0, priors)
