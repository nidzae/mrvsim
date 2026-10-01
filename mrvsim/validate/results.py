"""Validation result records (TDD section 9) consumed by pytest and by the Validation panel."""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

STATUSES = ("pass", "fail", "skipped", "error")
ALLOW_PLACEHOLDER_ENV = "MRVSIM_ALLOW_PLACEHOLDER_VALIDATION"


@dataclass
class ValidationResult:
    id: str                              # V1..V7
    name: str
    status: str                          # pass | fail | skipped | error
    pass_rule: str
    compared: dict[str, Any] = field(default_factory=dict)   # what was compared to what
    reason: str = ""
    citations: list[str] = field(default_factory=list)
    target_status: str = ""              # ok | verify | missing
    diagnostic_only: bool = False        # True when run against PLACEHOLDER priors
    elapsed_s: float | None = None
    run_utc: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def placeholder_allowed() -> bool:
    return os.environ.get(ALLOW_PLACEHOLDER_ENV, "").lower() in {"1", "true", "yes"}


def refusal(vid: str, name: str, pass_rule: str, citations: list[str], reason: str) -> ValidationResult:
    return ValidationResult(vid, name, "skipped", pass_rule, {}, reason, citations)


def write_results(results: list[ValidationResult], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                "results": [r.to_dict() for r in results]}, indent=2, default=_default), encoding="utf-8")
    return path


def _default(o: Any) -> Any:
    import numpy as np

    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, float) and o != o:
        return None
    return str(o)
