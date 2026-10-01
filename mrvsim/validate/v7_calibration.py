"""V7: calibration on the default configuration (TDD section 9)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from mrvsim.io.config import load_config
from mrvsim.pipeline import run_scored
from mrvsim.sensors import load_library
from mrvsim.validate.common import default_priors, finish, guard_placeholder
from mrvsim.validate.results import ValidationResult
from mrvsim.validate.targets import load_targets

VID, NAME = "V7", "Calibration on the default configuration"
DEFAULT_POLICY = {"sensors": {"bridger_gml": {"coverage": 1.0, "frequency_per_year": 2},
                              "ghgsat_c": {"coverage": 0.3, "frequency_per_year": 12, "targeting": "throughput"},
                              "tropomi": {"coverage": 1.0}}}


def run(targets: dict[str, Any] | None = None, replications: int = 20, n_draws: int = 2000, facilities_per_stratum: int | None = 10,
        n_per_stratum: int | None = None, root: str = "runs") -> ValidationResult:
    t0 = time.perf_counter()
    tg = (targets or load_targets())["V7"]
    priors = default_priors()
    ref = guard_placeholder(VID, NAME, tg["pass_rule"], tg["citations"], priors)
    if ref:
        return ref
    cfg = load_config(Path(__file__).resolve().parents[2] / "configs" / "default.yaml")
    over: dict[str, Any] = {"name": "v7-calibration", "replications": replications}
    if not cfg.policy.get("sensors"):
        over["policy"] = DEFAULT_POLICY
    if n_per_stratum:
        over["population"] = {**cfg.population, "n_per_stratum": n_per_stratum}
    cfg = cfg.with_overrides(**over)
    rep, run_ctx, _ = run_scored(cfg, root=root, replications=replications, n_draws=n_draws, facilities_per_stratum=facilities_per_stratum,
                                 library=load_library(), keep_last=False)
    lo, hi = tg["kappa_range"]; compared: dict[str, Any] = {"run_id": run_ctx.run_id, "replications": replications}; verdicts = []
    for kpi in ("mass", "intensity"):
        m = rep.kpi[kpi]["calibration"]; ok = lo <= m.mean <= hi
        compared[kpi] = {"kappa": m.mean, "se": m.se, "range": [lo, hi], "pass": bool(ok), "width_median": rep.kpi[kpi]["width_median"].mean}
        verdicts.append(bool(ok))
    return finish(ValidationResult(VID, NAME, "pass" if all(verdicts) else "fail", tg["pass_rule"], compared, "", tg["citations"], "ok"), t0, priors)
