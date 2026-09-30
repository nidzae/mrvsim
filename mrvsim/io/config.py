"""Run configuration: loading, validation of the top-level shape, canonical hashing.

A run config is YAML. Top-level sections mirror the pipeline stages in
TDD section 1 (population, sensors, policy, estimator, scoring). This module
validates only the shape needed for reproducibility (seed, year, section
presence); each stage validates its own section when it is built.

Config over code (CLAUDE.md conventions): anything a user might change lives in
YAML, so every stage reads its parameters from the ``RunConfig`` passed to it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

# Sections a run config may carry. Unknown sections are rejected so that typos
# ("sensor:" for "sensors:") fail loudly instead of silently using defaults.
KNOWN_SECTIONS: frozenset[str] = frozenset(
    {"name", "seed", "year", "replications", "population", "sensors", "policy", "estimator", "scoring", "notes"}
)


class ConfigError(ValueError):
    """Raised when a run configuration is malformed."""


@dataclass(frozen=True)
class RunConfig:
    """Validated run configuration.

    Attributes
    ----------
    seed:
        Master seed (PRD N4). Required; there is no default so that a run can
        never be accidentally unseeded.
    year:
        Simulated calendar year; drives satellite ephemerides and conditions.
    replications:
        Monte Carlo replications R (TDD section 7; default 200, interactive 20).
    population, sensors, policy, estimator, scoring:
        Stage sections, passed through as mappings and validated by each stage.
    """

    seed: int
    year: int = 2024
    name: str = "unnamed"
    replications: int = 200
    population: Mapping[str, Any] = field(default_factory=dict)
    sensors: Mapping[str, Any] = field(default_factory=dict)
    policy: Mapping[str, Any] = field(default_factory=dict)
    estimator: Mapping[str, Any] = field(default_factory=dict)
    scoring: Mapping[str, Any] = field(default_factory=dict)
    notes: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RunConfig":
        if not isinstance(raw, Mapping):
            raise ConfigError(f"config must be a mapping, got {type(raw).__name__}")
        unknown = set(raw) - KNOWN_SECTIONS
        if unknown:
            raise ConfigError(f"unknown config section(s): {sorted(unknown)}; known: {sorted(KNOWN_SECTIONS)}")
        if "seed" not in raw:
            raise ConfigError("config must set 'seed' (PRD N4: all randomness is seeded)")
        seed = raw["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ConfigError(f"'seed' must be an integer, got {seed!r}")
        if seed < 0 or seed >= 2**63:
            raise ConfigError("'seed' must be in [0, 2**63-1]")
        year = int(raw.get("year", 2024))
        if year < 2000 or year > 2100:
            raise ConfigError(f"'year' out of range: {year}")
        reps = int(raw.get("replications", 200))
        if reps < 1:
            raise ConfigError("'replications' must be >= 1")
        for sec in ("population", "sensors", "policy", "estimator", "scoring"):
            if sec in raw and raw[sec] is not None and not isinstance(raw[sec], Mapping):
                raise ConfigError(f"section '{sec}' must be a mapping")
        return cls(
            seed=seed,
            year=year,
            name=str(raw.get("name", "unnamed")),
            replications=reps,
            population=dict(raw.get("population") or {}),
            sensors=dict(raw.get("sensors") or {}),
            policy=dict(raw.get("policy") or {}),
            estimator=dict(raw.get("estimator") or {}),
            scoring=dict(raw.get("scoring") or {}),
            notes=str(raw.get("notes", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "seed": self.seed,
            "year": self.year,
            "replications": self.replications,
            "population": _plain(self.population),
            "sensors": _plain(self.sensors),
            "policy": _plain(self.policy),
            "estimator": _plain(self.estimator),
            "scoring": _plain(self.scoring),
            "notes": self.notes,
        }

    def with_overrides(self, **changes: Any) -> "RunConfig":
        """Return a copy with top-level fields replaced (e.g. ``seed=...``)."""
        d = self.to_dict()
        d.update(changes)
        return RunConfig.from_dict(d)


def _plain(obj: Any) -> Any:
    """Recursively convert mappings/sequences to plain dict/list for YAML."""
    if isinstance(obj, Mapping):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    return obj


def load_config(path: str | Path) -> RunConfig:
    """Load and validate a YAML run config."""
    p = Path(path)
    with p.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if raw is None:
        raise ConfigError(f"{p} is empty")
    try:
        return RunConfig.from_dict(raw)
    except ConfigError as e:
        raise ConfigError(f"{p}: {e}") from e


def canonical_yaml(cfg: RunConfig | Mapping[str, Any]) -> str:
    """Byte-stable YAML serialisation (sorted keys, no aliases, block style)."""
    d = cfg.to_dict() if isinstance(cfg, RunConfig) else _plain(cfg)
    return yaml.safe_dump(d, sort_keys=True, default_flow_style=False, allow_unicode=True, width=1000)


def config_hash(cfg: RunConfig | Mapping[str, Any]) -> str:
    """SHA-256 of the canonical YAML; identifies a config independent of file formatting."""
    return hashlib.sha256(canonical_yaml(cfg).encode("utf-8")).hexdigest()
