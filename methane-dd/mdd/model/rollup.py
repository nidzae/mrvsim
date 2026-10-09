"""Per-asset components and the Monte Carlo roll-up (SPEC sections 6c, 6d, 7).

Each asset's annual emission is the sum of three components:
- large detected sources (floor): 8760 p Q with p and Q from their posteriors;
- large undetected sources (ceiling): the same with the zero-detection posterior, so that with n clear looks and
  good POD the 95th percentile falls roughly as 3/n [rule-of-three];
- sub-threshold emissions: supplier measurements, an area-flux product, or a segment literature fraction (a prior).

The draws of every asset are summed with the customer share, divided by throughput, and read off as percentiles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

HOURS = 8760.0
CH4_KG_PER_MMBTU = 19.26          # kg CH4 per MMBtu of methane at ~1,010 Btu/scf and 0.6785 kg/m3; unit conversion
MJ_PER_MMBTU = 1055.06
GWP100_FOSSIL_CH4 = 29.8          # [gwp-ar6]


@dataclass
class AssetDraws:
    asset_id: str
    segment: str
    customer_share: float
    throughput_ch4_kg_yr: float
    floor_kg_yr: np.ndarray             # (n,) detected large sources
    ceiling_kg_yr: np.ndarray           # (n,) undetected large sources
    sub_kg_yr: np.ndarray               # (n,) sub-threshold
    sub_basis: str                      # "supplier", "area_flux", "literature_prior", "none"
    attribution_prob: float = 1.0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def total_kg_yr(self) -> np.ndarray:
        return self.floor_kg_yr + self.ceiling_kg_yr + self.sub_kg_yr

    def measured_share(self) -> float:
        """Share of the asset's 95th-percentile emission that comes from measured components."""
        tot = np.quantile(self.total_kg_yr, 0.95)
        if tot <= 0:
            return 1.0
        measured = np.quantile(self.floor_kg_yr, 0.95) + (np.quantile(self.sub_kg_yr, 0.95) if self.sub_basis == "supplier" else 0.0)
        return float(min(measured / tot, 1.0))


def asset_draws(rng: np.random.Generator, asset_id: str, segment: str, customer_share: float, throughput_ch4_kg_yr: float,
                duty_detected, rate_detected, duty_zero, rate_zero, sub_kg_yr: np.ndarray | float, sub_basis: str,
                attribution_prob: float, n: int, meta: dict[str, Any] | None = None) -> AssetDraws:
    """Draws for one asset. ``duty_detected``/``rate_detected`` are None when the asset has no detections."""
    floor = np.zeros(n)
    if duty_detected is not None and rate_detected is not None:
        attrib = rng.random(n) < attribution_prob                                   # SPEC 6e: carry attribution through
        floor = np.where(attrib, HOURS * duty_detected.sample(rng, n) * rate_detected.sample(rng, n), 0.0)
    ceiling = HOURS * duty_zero.sample(rng, n) * rate_zero.sample(rng, n) if duty_zero is not None and rate_zero is not None else np.zeros(n)
    sub = np.broadcast_to(np.asarray(sub_kg_yr, dtype=float), (n,)).copy()
    return AssetDraws(asset_id, segment, customer_share, throughput_ch4_kg_yr, floor, ceiling, sub, sub_basis, attribution_prob, meta or {})


@dataclass
class ChainResult:
    delivered_ch4_kg_yr: float
    delivered_intensity: np.ndarray          # (n,) fraction
    segment_intensity: dict[str, np.ndarray] # segment -> (n,)
    assets: list[AssetDraws]

    def percentiles(self, x: np.ndarray, ps=(5, 50, 95)) -> dict[str, float]:
        return {f"p{p}": float(np.percentile(x, p)) for p in ps}

    def measured_floor_intensity(self) -> float:
        """5th percentile of the delivered intensity from the floor components alone (SPEC 7a)."""
        floor = sum(a.customer_share * a.floor_kg_yr for a in self.assets)
        return float(np.percentile(floor, 5) / self.delivered_ch4_kg_yr)

    def evidence_share(self) -> float:
        """Share of the assured (p95) delivered intensity that comes from measured components."""
        total = np.percentile(sum(a.customer_share * a.total_kg_yr for a in self.assets), 95)
        if total <= 0:
            return 1.0
        measured = np.percentile(sum(a.customer_share * (a.floor_kg_yr + (a.sub_kg_yr if a.sub_basis == "supplier" else 0.0)) for a in self.assets), 95)
        return float(min(measured / total, 1.0))

    def contributions(self) -> list[dict[str, Any]]:
        """Each asset's share of the assured delivered intensity (mean over draws of its weighted emission at the p95 level)."""
        total = float(np.percentile(sum(a.customer_share * a.total_kg_yr for a in self.assets), 95))
        rows = []
        for a in self.assets:
            e = a.customer_share * a.total_kg_yr
            rows.append({"asset_id": a.asset_id, "segment": a.segment, "p50_kg_yr": float(np.percentile(a.total_kg_yr, 50)),
                         "p95_kg_yr": float(np.percentile(a.total_kg_yr, 95)), "share_of_assured": float(np.percentile(e, 95) / total) if total > 0 else 0.0,
                         "floor_p5_kg_yr": float(np.percentile(a.floor_kg_yr, 5)), "measured_share": a.measured_share(), "sub_basis": a.sub_basis, **a.meta})
        return sorted(rows, key=lambda r: -r["share_of_assured"])


def roll_up(assets: list[AssetDraws], delivered_ch4_kg_yr: float) -> ChainResult:
    """Chain intensity draws (SPEC 7a): sum over assets of share x emission, over methane delivered at the meter."""
    if not assets:
        raise ValueError("no assets")
    n = assets[0].total_kg_yr.shape[0]
    seg: dict[str, np.ndarray] = {}
    seg_thr: dict[str, float] = {}
    total = np.zeros(n)
    for a in assets:
        e = a.customer_share * a.total_kg_yr
        total += e
        seg[a.segment] = seg.get(a.segment, np.zeros(n)) + e
        seg_thr[a.segment] = seg_thr.get(a.segment, 0.0) + a.customer_share * a.throughput_ch4_kg_yr
    seg_int = {s: seg[s] / seg_thr[s] if seg_thr[s] > 0 else np.full(n, np.nan) for s in seg}
    return ChainResult(delivered_ch4_kg_yr, total / delivered_ch4_kg_yr, seg_int, assets)


def intensity_units(fraction: float) -> dict[str, float]:
    """Percent of methane, kg CH4 per MMBtu delivered, g CO2e per MJ (GWP100 29.8) (SPEC 7a)."""
    kg_per_mmbtu = fraction * CH4_KG_PER_MMBTU
    return {"percent": fraction * 100.0, "kg_ch4_per_mmbtu": kg_per_mmbtu, "g_co2e_per_mj": kg_per_mmbtu * 1000.0 * GWP100_FOSSIL_CH4 / MJ_PER_MMBTU}
