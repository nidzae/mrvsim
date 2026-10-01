"""Run validation tests V1-V7 and write ``runs/validation/<timestamp>.json`` for the Validation panel (PRD F14)."""

from __future__ import annotations

import datetime as dt
import traceback
from pathlib import Path
from typing import Any, Callable

from mrvsim.validate import v1_rates, v2_totals, v3_intensity, v4_stability, v5_misspecification, v6_transient, v7_calibration
from mrvsim.validate.results import ValidationResult, write_results
from mrvsim.validate.targets import load_targets

_REPO = Path(__file__).resolve().parents[2]
MODULES: dict[str, Callable[..., ValidationResult]] = {
    "V1": v1_rates.run, "V2": v2_totals.run, "V3": v3_intensity.run, "V4": v4_stability.run,
    "V5": v5_misspecification.run, "V6": v6_transient.run, "V7": v7_calibration.run,
}
QUICK_KWARGS: dict[str, dict[str, Any]] = {   # reduced settings for an interactive run of the panel
    "V1": {"n_per_stratum": 60}, "V2": {"n_per_stratum": 60}, "V3": {"n_per_stratum": 30, "n_draws": 1500},
    "V4": {"replications": 2, "facilities_per_stratum": 5, "n_draws": 1000}, "V5": {"n_per_stratum": 30, "n_draws": 1000, "step": 10},
    "V6": {"n_events": 60}, "V7": {"replications": 3, "facilities_per_stratum": 5, "n_draws": 1000},
}


def run_all(ids: list[str] | None = None, quick: bool = False, out_dir: Path | None = None) -> tuple[list[ValidationResult], Path]:
    targets = load_targets()
    results: list[ValidationResult] = []
    for vid in ids or list(MODULES):
        fn = MODULES[vid]
        try:
            kwargs = QUICK_KWARGS.get(vid, {}) if quick else {}
            results.append(fn(targets, **kwargs))
        except Exception as e:  # noqa: BLE001 - a crashing test must not hide the others
            results.append(ValidationResult(vid, fn.__module__, "error", targets[vid]["pass_rule"], {}, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-800:]}", targets[vid]["citations"]))
    out_dir = out_dir or (_REPO / "runs" / "validation")
    path = write_results(results, out_dir / f"{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json")
    return results, path


def latest_results_path(out_dir: Path | None = None) -> Path | None:
    d = out_dir or (_REPO / "runs" / "validation")
    files = sorted(d.glob("*.json")) if d.exists() else []
    return files[-1] if files else None


if __name__ == "__main__":
    import argparse, json  # noqa: E401

    ap = argparse.ArgumentParser(); ap.add_argument("--ids", nargs="*"); ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    res, p = run_all(a.ids, a.quick)
    for r in res:
        print(f"{r.id}: {r.status:8s} {r.reason[:100]}")
    print("written", p)
