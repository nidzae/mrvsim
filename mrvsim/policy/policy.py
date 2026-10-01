"""Policy parameters and tip-and-cue rules (TDD section 8.1-8.2), serialisable to YAML (CLAUDE.md Phase 7)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

Targeting = Literal["random", "throughput", "prior_risk", "widest_interval"]
CoverageBasis = Literal["facilities", "throughput"]
RuleKind = Literal["satellite_detect_to_aircraft", "cms_run_length_to_drone", "budget_to_widest_interval"]


@dataclass
class SensorPolicy:
    """Per-sensor-class controls (TDD section 8.1 table)."""

    enabled: bool = True
    coverage: float = 1.0                 # share of facilities (or throughput) covered
    coverage_basis: str = "facilities"
    frequency_per_year: int = 1           # surveys per year (aircraft, drone, OGI); taskings per facility (satellite)
    targeting: str = "random"
    scheduling: str = "independent"       # campaign/survey sensors: independent dates | regional campaign (DECISION_LOG 2026-10-01)
    campaign_days: int = 5                # window length of a basin campaign, days

    def validate(self, key: str) -> None:
        if self.scheduling not in ("independent", "campaign"):
            raise ValueError(f"{key}: scheduling must be independent|campaign")
        if not 1 <= int(self.campaign_days) <= 60:
            raise ValueError(f"{key}: campaign_days must be in [1, 60]")
        if not 0.0 <= self.coverage <= 1.0:
            raise ValueError(f"{key}: coverage must be in [0, 1]")
        if self.coverage_basis not in ("facilities", "throughput"):
            raise ValueError(f"{key}: coverage_basis must be facilities|throughput")
        if not 0 <= self.frequency_per_year <= 52:
            raise ValueError(f"{key}: frequency_per_year must be in [0, 52] (TDD section 8.1)")
        if self.targeting not in ("random", "throughput", "prior_risk", "widest_interval"):
            raise ValueError(f"{key}: unknown targeting {self.targeting!r}")


@dataclass
class Rule:
    """Adaptive rule evaluated daily inside the simulation (TDD section 8.2).

    kinds and parameters:
      satellite_detect_to_aircraft: IF trigger_sensor detects rate >= X (kg/h) THEN schedule target_sensor within N days
      cms_run_length_to_drone:      IF CMS detected run >= H hours THEN schedule target_sensor within N days
      budget_to_widest_interval:    monthly, allocate `budget_per_month` target_sensor visits to the widest posterior intervals
    """

    kind: str
    trigger_sensor: str = ""
    target_sensor: str = ""
    X_kg_h: float = 100.0
    N_days: int = 14
    H_hours: int = 24
    budget_per_month: int = 0
    max_visits_per_facility: int = 4
    cooldown_days: int = 30            # do not re-cue the same facility within this window

    def validate(self) -> None:
        if self.kind not in ("satellite_detect_to_aircraft", "cms_run_length_to_drone", "budget_to_widest_interval"):
            raise ValueError(f"unknown rule kind {self.kind!r}")
        if not self.target_sensor:
            raise ValueError("rule needs a target_sensor")
        if self.kind != "budget_to_widest_interval" and not self.trigger_sensor:
            raise ValueError(f"{self.kind} needs a trigger_sensor")
        if self.N_days < 1 or self.cooldown_days < 0 or self.max_visits_per_facility < 1:
            raise ValueError("N_days >= 1, cooldown_days >= 0, max_visits_per_facility >= 1")


@dataclass
class Policy:
    sensors: dict[str, SensorPolicy] = field(default_factory=dict)
    rules: list[Rule] = field(default_factory=list)
    min_tier: str = "A"                  # validation-tier filter (PRD F6)
    name: str = "policy"
    incidental_capture: bool = True      # neighbours inside a tasked scene / survey block are observed too (DECISION_LOG 2026-10-01)

    def validate(self) -> None:
        for k, s in self.sensors.items():
            s.validate(k)
        for r in self.rules:
            r.validate()
            if r.target_sensor not in self.sensors:
                raise ValueError(f"rule target {r.target_sensor!r} is not a configured sensor")
            if r.trigger_sensor and r.trigger_sensor not in self.sensors:
                raise ValueError(f"rule trigger {r.trigger_sensor!r} is not a configured sensor")
        if self.min_tier not in "ABCD":
            raise ValueError("min_tier must be A-D")

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "min_tier": self.min_tier, "sensors": {k: asdict(v) for k, v in self.sensors.items()},
                "rules": [asdict(r) for r in self.rules], "incidental_capture": self.incidental_capture}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Policy":
        sensors = {k: SensorPolicy(**v) for k, v in (d.get("sensors") or {}).items()}
        rules = [Rule(**r) for r in (d.get("rules") or [])]
        p = cls(sensors=sensors, rules=rules, min_tier=str(d.get("min_tier", "A")), name=str(d.get("name", "policy")),
                incidental_capture=bool(d.get("incidental_capture", True)))
        p.validate()
        return p

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False, default_flow_style=False)

    @classmethod
    def from_yaml(cls, text: str) -> "Policy":
        return cls.from_dict(yaml.safe_load(text) or {})

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_yaml(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Policy":
        return cls.from_yaml(Path(path).read_text(encoding="utf-8"))

    # -- bridge to the observation simulator's plan builder -------------------
    def to_policy_cfg(self) -> dict[str, Any]:
        """The ``policy`` config section understood by ``mrvsim.observe.build_plan``."""
        return {"sensors": {k: {"enabled": s.enabled, "coverage": s.coverage, "coverage_basis": s.coverage_basis,
                                "frequency_per_year": s.frequency_per_year, "targeting": s.targeting,
                                "scheduling": s.scheduling, "campaign_days": s.campaign_days}
                            for k, s in self.sensors.items()},
                "rules": [asdict(r) for r in self.rules], "min_tier": self.min_tier, "incidental_capture": self.incidental_capture}

    @classmethod
    def from_policy_cfg(cls, cfg: dict[str, Any], name: str = "policy") -> "Policy":
        d = {"name": name, "min_tier": cfg.get("min_tier", "A"), "sensors": {}, "rules": cfg.get("rules") or [],
             "incidental_capture": bool(cfg.get("incidental_capture", True))}
        for k, v in (cfg.get("sensors") or {}).items():
            d["sensors"][k] = {"enabled": v.get("enabled", True), "coverage": v.get("coverage", 1.0), "coverage_basis": v.get("coverage_basis", "facilities"),
                               "frequency_per_year": v.get("frequency_per_year", 1), "targeting": v.get("targeting", "random"),
                               "scheduling": v.get("scheduling", "independent"), "campaign_days": v.get("campaign_days", 5)}
        return cls.from_dict(d)

    def certification_sensors(self, library) -> list[str]:  # noqa: ANN001
        """Deployed sensors allowed to count toward certification at this policy's tier filter (PRD N2)."""
        allowed = {s.key for s in library.certifiable(self.min_tier)}
        return [k for k, s in self.sensors.items() if s.enabled and k in allowed]
