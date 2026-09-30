"""Sensor record schema (TDD section 4 table) and YAML loader.

One YAML per sensor class in ``configs/sensors/``. Every numeric block carries
``citations`` (keys in ``docs/REFERENCES.md``) and a ``provenance`` record whose
``status`` is ``fitted`` (from a blind-release table via ``mrvsim.sensors.fit``),
``summary`` (derived from published summary statistics), or ``assumption``. The
attribution panel flags anything that is not ``fitted`` or whose citation is
``verify`` in REFERENCES.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

import yaml

from mrvsim.sensors.pod import PODCurve, SurfaceAdjustment
from mrvsim.sensors.quantification import QuantificationError

Tier = Literal["A", "B", "C", "D"]
ObservationMode = Literal["snapshot", "continuous", "survey"]
SpatialScope = Literal["site", "source"]
Schedule = Literal["orbit", "campaign", "survey", "hourly"]
SensorClass = Literal["aircraft", "satellite", "cms", "drone", "ogi"]

_TIERS = ("A", "B", "C", "D")


class SensorSchemaError(ValueError):
    pass


@dataclass(frozen=True)
class Provenance:
    status: str            # fitted | summary | assumption
    basis: str             # human-readable derivation
    fit_record: Mapping[str, Any] | None = None

    def validate(self, where: str) -> None:
        if self.status not in ("fitted", "summary", "assumption"):
            raise SensorSchemaError(f"{where}: provenance.status must be fitted|summary|assumption, got {self.status!r}")
        if not self.basis:
            raise SensorSchemaError(f"{where}: provenance.basis is required")


@dataclass(frozen=True)
class FalsePositive:
    rate_per_opportunity: float                 # lambda_FP,s (TDD section 4.4)
    reported_rate_quantile_max: float           # false calls report a rate from the lower part of the detected-rate distribution
    citations: tuple[str, ...]
    provenance: Provenance


@dataclass(frozen=True)
class Constraints:
    max_wind_m_s: float | None
    max_solar_zenith_deg: float | None
    max_cloud_fraction: float | None            # opportunity unusable if the cloud draw says cloudy (satellites/aircraft)
    day_only: bool
    outage_probability: float | None = None     # CMS (TDD section 5.2)
    wind_sector_coverage: float | None = None   # CMS: fraction of wind directions covered by sensor placement


@dataclass(frozen=True)
class Orbit:
    tle_name: str                  # name as it appears in Celestrak TLE files [celestrak]
    norad_id: int | None
    swath_km: float
    tasked: bool                   # tasked (GHGSat, PRISMA, EnMAP, Tanager) vs. wall-to-wall (TROPOMI, S2, Landsat)
    local_time_desc: str           # descriptive, e.g. "10:30 descending"
    citations: tuple[str, ...]


@dataclass(frozen=True)
class Cost:
    per_site_visit_usd: float | None
    per_tasking_usd: float | None
    per_site_year_usd: float | None
    citations: tuple[str, ...]
    provenance: Provenance


@dataclass(frozen=True)
class Sensor:
    key: str
    name: str
    sensor_class: str
    tier: str
    enabled_for_certification: bool
    observation_mode: str
    spatial_scope: str
    schedule: str
    pod: PODCurve
    pod_citations: tuple[str, ...]
    pod_provenance: Provenance
    quantification: QuantificationError
    quantification_tier: str
    quantification_citations: tuple[str, ...]
    quantification_provenance: Provenance
    false_positive: FalsePositive
    constraints: Constraints
    cost: Cost
    orbit: Orbit | None = None
    notes: str = ""
    source_path: str = ""

    def citation_keys(self) -> tuple[str, ...]:
        keys = set(self.pod_citations) | set(self.quantification_citations) | set(self.false_positive.citations) | set(self.cost.citations)
        if self.orbit:
            keys |= set(self.orbit.citations)
        return tuple(sorted(keys))

    def unverified_blocks(self) -> tuple[str, ...]:
        out = []
        for name, prov in (("pod", self.pod_provenance), ("quantification", self.quantification_provenance),
                           ("false_positive", self.false_positive.provenance), ("cost", self.cost.provenance)):
            if prov.status != "fitted":
                out.append(name)
        return tuple(out)


def _prov(raw: Mapping[str, Any], where: str) -> Provenance:
    if not isinstance(raw, Mapping):
        raise SensorSchemaError(f"{where}: provenance block missing")
    p = Provenance(status=str(raw.get("status", "")), basis=str(raw.get("basis", "")), fit_record=raw.get("fit_record"))
    p.validate(where)
    return p


def _cites(raw: Any, where: str) -> tuple[str, ...]:
    if not raw or not isinstance(raw, list):
        raise SensorSchemaError(f"{where}: 'citations' must be a non-empty list (PRD N1)")
    return tuple(str(c) for c in raw)


def _opt_float(v: Any) -> float | None:
    return None if v is None else float(v)


def load_sensor(path: str | Path) -> Sensor:
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    key = raw.get("key") or p.stem
    w = f"sensor {key}"
    for req in ("name", "class", "tier", "enabled_for_certification", "observation_mode", "spatial_scope", "schedule",
                "pod", "quantification", "false_positive", "constraints", "cost"):
        if req not in raw:
            raise SensorSchemaError(f"{w}: missing required field '{req}' (TDD section 4 table)")
    tier = str(raw["tier"])
    if tier not in _TIERS:
        raise SensorSchemaError(f"{w}: tier must be one of {_TIERS}")
    if raw["observation_mode"] not in ("snapshot", "continuous", "survey"):
        raise SensorSchemaError(f"{w}: bad observation_mode")
    if raw["spatial_scope"] not in ("site", "source"):
        raise SensorSchemaError(f"{w}: bad spatial_scope")
    if raw["schedule"] not in ("orbit", "campaign", "survey", "hourly"):
        raise SensorSchemaError(f"{w}: bad schedule")
    if raw["class"] not in ("aircraft", "satellite", "cms", "drone", "ogi"):
        raise SensorSchemaError(f"{w}: bad class")
    enabled = bool(raw["enabled_for_certification"])
    if tier != "A" and enabled:
        raise SensorSchemaError(f"{w}: tier {tier} sensors must have enabled_for_certification: false (PRD N2)")

    pr = raw["pod"]
    sa = pr.get("surface_adjustment") or {}
    surface = SurfaceAdjustment(
        enabled=bool(sa.get("enabled", False)), rho_ref=float(sa.get("rho_ref", 0.3)), rho_p10=float(sa.get("rho_p10", 0.12)),
        phi_at_p10=float(sa.get("phi_at_p10", 0.5)), uncertainty_halfwidth=float(sa.get("uncertainty_halfwidth", 0.5)),
    )
    if "a" in pr and "b" in pr:
        a, b = float(pr["a"]), float(pr["b"])
    elif "pod50_kg_h" in pr and "pod90_kg_h" in pr:
        a, b = PODCurve.ab_from_pod50_pod90(float(pr["pod50_kg_h"]), float(pr["pod90_kg_h"]))
    else:
        raise SensorSchemaError(f"{w}: pod needs (a, b) or (pod50_kg_h, pod90_kg_h)")
    cov = pr.get("ab_cov")
    pod = PODCurve(a=a, b=b, gamma=float(pr.get("gamma", 1.0)), u_ref_m_s=float(pr.get("u_ref_m_s", 3.0)), surface=surface,
                   ab_cov=tuple(map(tuple, cov)) if cov else None,  # type: ignore[arg-type]
                   u_min_m_s=float(pr.get("u_min_m_s", 2.0)))

    qr = raw["quantification"]
    if "beta" in qr and "sigma" in qr:
        quant = QuantificationError(float(qr["beta"]), float(qr["sigma"]))
    elif "ratio_95_low" in qr and "ratio_95_high" in qr:
        quant = QuantificationError.from_ratio_interval(float(qr["ratio_95_low"]), float(qr["ratio_95_high"]))
    else:
        raise SensorSchemaError(f"{w}: quantification needs (beta, sigma) or (ratio_95_low, ratio_95_high)")
    q_tier = str(qr.get("tier", tier))
    if q_tier not in _TIERS:
        raise SensorSchemaError(f"{w}: quantification.tier must be one of {_TIERS}")

    fr = raw["false_positive"]
    fp = FalsePositive(
        rate_per_opportunity=float(fr["rate_per_opportunity"]),
        reported_rate_quantile_max=float(fr.get("reported_rate_quantile_max", 0.25)),
        citations=_cites(fr.get("citations"), f"{w}.false_positive"), provenance=_prov(fr.get("provenance"), f"{w}.false_positive"),
    )
    if not 0.0 <= fp.rate_per_opportunity <= 1.0:
        raise SensorSchemaError(f"{w}: false_positive.rate_per_opportunity must be in [0, 1]")

    cr = raw["constraints"]
    cons = Constraints(
        max_wind_m_s=_opt_float(cr.get("max_wind_m_s")), max_solar_zenith_deg=_opt_float(cr.get("max_solar_zenith_deg")),
        max_cloud_fraction=_opt_float(cr.get("max_cloud_fraction")), day_only=bool(cr.get("day_only", False)),
        outage_probability=_opt_float(cr.get("outage_probability")), wind_sector_coverage=_opt_float(cr.get("wind_sector_coverage")),
    )

    co = raw["cost"]
    cost = Cost(
        per_site_visit_usd=_opt_float(co.get("per_site_visit_usd")), per_tasking_usd=_opt_float(co.get("per_tasking_usd")),
        per_site_year_usd=_opt_float(co.get("per_site_year_usd")), citations=_cites(co.get("citations"), f"{w}.cost"),
        provenance=_prov(co.get("provenance"), f"{w}.cost"),
    )
    if all(v is None for v in (cost.per_site_visit_usd, cost.per_tasking_usd, cost.per_site_year_usd)):
        raise SensorSchemaError(f"{w}: cost needs at least one of per_site_visit_usd, per_tasking_usd, per_site_year_usd")

    orbit = None
    if raw["class"] == "satellite":
        orr = raw.get("orbit")
        if not orr:
            raise SensorSchemaError(f"{w}: satellites need an 'orbit' block (TDD section 5.1)")
        orbit = Orbit(
            tle_name=str(orr["tle_name"]), norad_id=None if orr.get("norad_id") is None else int(orr["norad_id"]),
            swath_km=float(orr["swath_km"]), tasked=bool(orr["tasked"]), local_time_desc=str(orr.get("local_time_desc", "")),
            citations=_cites(orr.get("citations"), f"{w}.orbit"),
        )

    return Sensor(
        key=str(key), name=str(raw["name"]), sensor_class=str(raw["class"]), tier=tier, enabled_for_certification=enabled,
        observation_mode=str(raw["observation_mode"]), spatial_scope=str(raw["spatial_scope"]), schedule=str(raw["schedule"]),
        pod=pod, pod_citations=_cites(pr.get("citations"), f"{w}.pod"), pod_provenance=_prov(pr.get("provenance"), f"{w}.pod"),
        quantification=quant, quantification_tier=q_tier, quantification_citations=_cites(qr.get("citations"), f"{w}.quantification"),
        quantification_provenance=_prov(qr.get("provenance"), f"{w}.quantification"),
        false_positive=fp, constraints=cons, cost=cost, orbit=orbit, notes=str(raw.get("notes", "")), source_path=str(p),
    )
