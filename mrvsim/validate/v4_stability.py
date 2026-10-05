"""V4: stratum stability, terciles -> quintiles (TDD section 3.1, 9)."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from mrvsim.pipeline import run_scored
from mrvsim.sensors import load_library
from mrvsim.validate.common import base_config, default_priors, finish, guard_placeholder
from mrvsim.validate.results import ValidationResult
from mrvsim.validate.targets import load_targets

VID, NAME = "V4", "Stratum stability (terciles vs quintiles)"
# Headline metrics of the dashboard. Completeness left the headline row on 2026-10-05 (PRD section 5.5) and is no
# longer part of this check; the decided share of emitted mass (PRD section 5.5a) replaced it.
HEADLINE = ("calibration", "width_median", "bias_median", "certified_share_weighted_throughput", "decided_share_emitted_mass")


def run(targets: dict[str, Any] | None = None, replications: int = 3, n_per_stratum: int = 30, n_draws: int = 2000,
        facilities_per_stratum: int | None = 10, root: str = "runs") -> ValidationResult:
    t0 = time.perf_counter()
    tg = (targets or load_targets())["V4"]
    priors = default_priors()
    ref = guard_placeholder(VID, NAME, tg["pass_rule"], tg["citations"], priors)
    if ref:
        return ref
    policy = {"sensors": {"bridger_gml": {"coverage": 1.0, "frequency_per_year": 2}, "cms_generic": {"coverage": 0.2, "targeting": "throughput"}}}
    lib = load_library()
    reports = {}
    for label, ncls in (("terciles", 3), ("quintiles", 5)):
        cfg = base_config(name=f"v4-{label}", population={"n_per_stratum": n_per_stratum, "n_throughput_classes": ncls}, policy=policy, replications=replications)
        rep, _, _ = run_scored(cfg, root=root, replications=replications, n_draws=n_draws, facilities_per_stratum=facilities_per_stratum, library=lib, keep_last=False)
        reports[label] = rep
    k = float(tg["se_multiplier"]); compared: dict[str, Any] = {}; verdicts = []
    for kpi in ("mass", "intensity"):
        for m in HEADLINE:
            a, b = reports["terciles"].kpi[kpi][m], reports["quintiles"].kpi[kpi][m]
            se = float(np.sqrt(np.nan_to_num(a.se) ** 2 + np.nan_to_num(b.se) ** 2))
            if m == "calibration":   # with few replications the MC SE understates sampling error; floor at the binomial SE over facilities
                n_fac = max(reports["terciles"].meta.get("n_scored", 300), 1)
                se = max(se, float(np.sqrt(2 * a.mean * (1 - a.mean) / n_fac)))
            diff = abs(a.mean - b.mean); ok = bool(diff <= k * se) if se > 0 else bool(diff == 0)
            compared[f"{kpi}.{m}"] = {"terciles": a.mean, "quintiles": b.mean, "diff": diff, "k_se": k * se, "pass": ok}
            verdicts.append(ok)
    return finish(ValidationResult(VID, NAME, "pass" if all(verdicts) else "fail", tg["pass_rule"], compared, "", tg["citations"], "ok"), t0, priors)
