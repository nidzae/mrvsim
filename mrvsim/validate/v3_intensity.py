"""V3: reproduce published measurement-informed basin intensities (TDD section 9) [haynesville-2025]."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from mrvsim.io.seeds import SeedTree
from mrvsim.pipeline import run_replication
from mrvsim.sensors import load_library
from mrvsim.validate.common import base_config, default_priors, finish, guard_placeholder
from mrvsim.validate.results import ValidationResult
from mrvsim.validate.targets import load_targets, target_missing, worst_status

VID, NAME = "V3", "Published basin intensities reproduced with the studies' sensor mix"


def basin_intensity_interval(res, basin_key: str) -> tuple[float, float, float, float]:
    """Throughput-weighted basin intensity: (p5, p50, p95, truth) from facility posteriors.

    The basin intensity is sum(mass)/sum(G); we approximate its posterior by weighting facility percentiles with
    marketed methane G_i (a conservative interval: it ignores averaging across independent facility errors).
    """
    pop, post = res.pop, res.post
    bi = list(pop.strata.basins).index(basin_key)
    sel = (pop.basin_idx == bi) & np.isfinite(post.intensity_p50)
    g = pop.throughput.g_ch4_kg_yr[sel] * pop.stratum_weights("throughput")[sel]
    w = g / g.sum()
    lo = float((w * post.intensity_p05[sel]).sum()); med = float((w * post.intensity_p50[sel]).sum()); hi = float((w * post.intensity_p95[sel]).sum())
    truth = float((w * pop.true_intensity()[sel]).sum())
    return lo, med, hi, truth


def run(targets: dict[str, Any] | None = None, n_per_stratum: int = 60, n_draws: int = 3000) -> ValidationResult:
    t0 = time.perf_counter()
    tg = (targets or load_targets())["V3"]
    priors = default_priors()
    ref = guard_placeholder(VID, NAME, tg["pass_rule"], tg["citations"], priors)
    if ref:
        return ref
    cfg = base_config(population={"n_per_stratum": n_per_stratum}, policy={"sensors": tg["sensor_mix"]}, estimator={"n_draws": n_draws})
    res = run_replication(cfg, SeedTree(cfg.seed), 0, load_library(), n_draws=n_draws)
    compared: dict[str, Any] = {}; verdicts = []; statuses = []
    for basin, blk in tg["intensities"].items():
        statuses.append(blk)
        lo, med, hi, truth = basin_intensity_interval(res, basin)
        entry = {"sim_interval": [lo, med, hi], "sim_truth": truth, "published": blk.get("value"), "published_ci": blk.get("ci_95")}
        if target_missing(blk):
            entry["pass"] = None
        else:
            ok = lo <= float(blk["value"]) <= hi
            entry["pass"] = bool(ok); verdicts.append(bool(ok))
        compared[basin] = entry
    bs = tg["boundary_sensitivity_factor"]; statuses.append(bs)
    compared["boundary_sensitivity"] = {"published": bs.get("value"), "pass": None, "note": "not computed: published factor missing" if target_missing(bs) else "TODO: implement basin-box shrink test"}
    ts = worst_status(statuses)
    status = "skipped" if not verdicts else ("pass" if all(verdicts) else "fail")
    reason = "" if verdicts else "all targets missing"
    return finish(ValidationResult(VID, NAME, status, tg["pass_rule"], compared, reason, tg["citations"], ts), t0, priors)
