"""KPI and grades (SPEC section 7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from mdd.model.rollup import ChainResult, intensity_units

GRADES = ("Verified", "Screened, unverified", "Flagged", "Failed")


@dataclass
class Scorecard:
    target: float
    delivered: dict[str, float]            # p5, p50, p95 of delivered intensity (fraction)
    assured_intensity: float               # p95
    measured_floor: float                  # p5 of the floor component alone
    evidence_share: float
    grade: str
    recommendation: str
    segments: dict[str, dict[str, Any]]
    units: dict[str, float]
    contributions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"target": self.target, "delivered": self.delivered, "assured_intensity": self.assured_intensity, "measured_floor": self.measured_floor,
                "evidence_share": self.evidence_share, "grade": self.grade, "recommendation": self.recommendation, "segments": self.segments,
                "units": self.units, "contributions": self.contributions}


def grade_for(assured: float, floor: float, evidence_share: float, target: float, has_detections: bool, supplier_data: bool) -> str:
    if floor > target:
        return "Failed"
    if assured <= target and evidence_share >= 0.8 and supplier_data:
        return "Verified"
    if has_detections and assured > target:
        return "Flagged"
    return "Screened, unverified"


def score(chain: ChainResult, target: float, supplier_data: bool = False) -> Scorecard:
    pct = chain.percentiles(chain.delivered_intensity)
    assured = pct["p95"]
    floor = chain.measured_floor_intensity()
    ev = chain.evidence_share()
    has_det = any(np.percentile(a.floor_kg_yr, 95) > 0 for a in chain.assets)
    grade = grade_for(assured, floor, ev, target, has_det, supplier_data)
    rec = {"Verified": "go", "Screened, unverified": "conditional go: contract a measurement plan (SPEC 3b) before relying on the intensity",
           "Flagged": "hold: request event logs and remediation records for the flagged assets before buying",
           "Failed": "no-go: measured emissions alone exceed the target; exclude or price as high-intensity gas"}[grade]
    segs = {}
    for s, x in chain.segment_intensity.items():
        sp = chain.percentiles(x)
        seg_floor = float(np.percentile(sum(a.customer_share * a.floor_kg_yr for a in chain.assets if a.segment == s), 5) /
                          max(sum(a.customer_share * a.throughput_ch4_kg_yr for a in chain.assets if a.segment == s), 1e-9))
        seg_det = any(np.percentile(a.floor_kg_yr, 95) > 0 for a in chain.assets if a.segment == s)
        seg_ev = _segment_evidence(chain, s)
        segs[s] = {**sp, "assured": sp["p95"], "measured_floor": seg_floor, "evidence_share": seg_ev,
                   "grade": grade_for(sp["p95"], seg_floor, seg_ev, target, seg_det, supplier_data), "n_assets": sum(a.segment == s for a in chain.assets)}
    return Scorecard(target, pct, assured, floor, ev, grade, rec, segs, intensity_units(assured), chain.contributions())


def _segment_evidence(chain: ChainResult, segment: str) -> float:
    assets = [a for a in chain.assets if a.segment == segment]
    total = np.percentile(sum(a.customer_share * a.total_kg_yr for a in assets), 95)
    if total <= 0:
        return 1.0
    measured = np.percentile(sum(a.customer_share * (a.floor_kg_yr + (a.sub_kg_yr if a.sub_basis == "supplier" else 0.0)) for a in assets), 95)
    return float(min(measured / total, 1.0))
