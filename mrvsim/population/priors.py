"""Stratum hyperparameters (TDD sections 3.2-3.6, 6.2) loaded from ``configs/priors/*.yaml``.

The same numbers drive truth generation (population) and the estimator's prior
(empirical Bayes, TDD section 6.2). ``provenance: PLACEHOLDER`` files are
allowed for development but validation tests must refuse them (CLAUDE.md Phase 1);
use :func:`require_fitted`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import yaml

DEFAULT_PRIORS_PATH = Path(__file__).resolve().parents[2] / "configs" / "priors" / "placeholder_v0.yaml"

_SCALAR_KEYS = (
    "lambda_k", "p_intermittent", "mu_0", "sigma_0", "mu_1", "sigma_1", "q_tail", "alpha",
    "nu_on", "tau_on", "nu_off", "tau_off", "sigma_nu_on", "sigma_nu_off",
)
_THROUGHPUT_KEYS = ("ln_gas_m3_yr_mu", "ln_gas_m3_yr_sigma", "ln_oil_bbl_yr_mu", "ln_oil_bbl_yr_sigma")


class PlaceholderPriorsError(RuntimeError):
    """Raised when a computation that needs fitted priors is given placeholders."""


@dataclass(frozen=True)
class StratumPriors:
    """Hyperparameters for one basin x facility-type cell (throughput class handled by quantile truncation)."""

    lambda_k: float          # K = Poisson(lambda_k) + 1                        [sherwin2024; rutherford2021]
    p_intermittent: float    # P(z = 1)                                          [cusworth2022; duren2019]
    mu_0: float              # ln q, steady                                      [rutherford2021; sherwin2024]
    sigma_0: float
    mu_1: float              # ln q, intermittent                                [cusworth2022]
    sigma_1: float
    q_tail: float            # Pareto splice point (kg/h)                        [cusworth2022; sherwin2024]
    alpha: float             # Pareto tail index
    nu_on: float             # ln D_on (h)                                       [daniels2023; cms-duration-2024]
    tau_on: float
    nu_off: float            # ln D_off (h)                                      [duren2019; cusworth2022]
    tau_off: float
    sigma_nu_on: float       # between-source sd of nu_on within the stratum (pi heterogeneity)
    sigma_nu_off: float
    ln_gas_m3_yr_mu: float   # throughput                                        [ghgrp; state-production-data]
    ln_gas_m3_yr_sigma: float
    ln_oil_bbl_yr_mu: float
    ln_oil_bbl_yr_sigma: float

    @property
    def duty_cycle(self) -> float:
        """pi = E[D_on] / (E[D_on] + E[D_off]) with lognormal means (TDD section 3.4)."""
        e_on = float(np.exp(self.nu_on + 0.5 * self.tau_on**2))
        e_off = float(np.exp(self.nu_off + 0.5 * self.tau_off**2))
        return e_on / (e_on + e_off)

    def validate(self) -> None:
        if not (0.0 <= self.p_intermittent <= 1.0):
            raise ValueError("p_intermittent must be in [0, 1]")
        for k in ("sigma_0", "sigma_1", "tau_on", "tau_off", "ln_gas_m3_yr_sigma", "ln_oil_bbl_yr_sigma"):
            if getattr(self, k) <= 0:
                raise ValueError(f"{k} must be > 0")
        if self.sigma_nu_on < 0 or self.sigma_nu_off < 0:
            raise ValueError("sigma_nu_on / sigma_nu_off must be >= 0")
        if self.lambda_k < 0:
            raise ValueError("lambda_k must be >= 0")
        if self.q_tail <= 0 or self.alpha <= 0:
            raise ValueError("q_tail and alpha must be > 0")


@dataclass(frozen=True)
class ConditionPriors:
    """Observing-condition climatology for a basin (TDD section 3.6)."""

    cloud_monthly: tuple[float, ...]                 # [modis-cloud]
    wind_weibull_k: float                            # [era5]
    wind_weibull_lambda_monthly: tuple[float, ...]   # [era5]
    surface_reflectance: float                       # [landsat-composite]
    surface_heterogeneity: float                     # [landsat-composite]

    def validate(self) -> None:
        if len(self.cloud_monthly) != 12 or len(self.wind_weibull_lambda_monthly) != 12:
            raise ValueError("monthly condition arrays must have 12 entries")
        if not all(0.0 <= c <= 1.0 for c in self.cloud_monthly):
            raise ValueError("cloud fractions must be in [0, 1]")


@dataclass(frozen=True)
class PriorSet:
    provenance: str
    version: str
    source_path: str
    fit_provenance: Mapping[str, Any] | None
    cells: Mapping[tuple[str, str], StratumPriors]       # (basin, facility_type) -> priors
    conditions: Mapping[str, ConditionPriors]            # basin -> conditions
    citation_keys: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_placeholder(self) -> bool:
        return self.provenance.upper() == "PLACEHOLDER"

    def for_cell(self, basin: str, facility_type: str) -> StratumPriors:
        return self.cells[(basin, facility_type)]

    def conditions_for(self, basin: str) -> ConditionPriors:
        return self.conditions[basin]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provenance": self.provenance,
            "version": self.version,
            "source_path": self.source_path,
            "cells": {f"{b}/{f}": asdict(p) for (b, f), p in self.cells.items()},
            "conditions": {b: asdict(c) for b, c in self.conditions.items()},
        }


def _merge(base: Mapping[str, Any], *overrides: Mapping[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {k: (dict(v) if isinstance(v, Mapping) else v) for k, v in base.items()}
    for ov in overrides:
        if not ov:
            continue
        for k, v in ov.items():
            if isinstance(v, Mapping) and isinstance(out.get(k), dict):
                out[k].update(v)
            else:
                out[k] = v
    return out


def load_priors(path: str | Path, basins: list[str], facility_types: list[str]) -> PriorSet:
    """Load a priors YAML and expand defaults + overrides into one record per cell."""
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    defaults = raw["defaults"]
    ft_over = raw.get("facility_types") or {}
    b_over = raw.get("basins") or {}
    cells: dict[tuple[str, str], StratumPriors] = {}
    for b in basins:
        for f in facility_types:
            merged = _merge(defaults, ft_over.get(f), b_over.get(b))
            tp = merged.pop("throughput")
            kwargs = {k: float(merged[k]) for k in _SCALAR_KEYS}
            kwargs.update({k: float(tp[k]) for k in _THROUGHPUT_KEYS})
            sp = StratumPriors(**kwargs)
            sp.validate()
            cells[(b, f)] = sp
    cond_raw = raw.get("conditions") or {}
    cond_defaults = cond_raw.get("defaults") or {}
    cond_basins = cond_raw.get("basins") or {}
    conditions: dict[str, ConditionPriors] = {}
    for b in basins:
        m = _merge(cond_defaults, cond_basins.get(b))
        cp = ConditionPriors(
            cloud_monthly=tuple(float(x) for x in m["cloud_monthly"]),
            wind_weibull_k=float(m["wind_weibull_k"]),
            wind_weibull_lambda_monthly=tuple(float(x) for x in m["wind_weibull_lambda_monthly"]),
            surface_reflectance=float(m["surface_reflectance"]),
            surface_heterogeneity=float(m["surface_heterogeneity"]),
        )
        cp.validate()
        conditions[b] = cp
    keys = _extract_citation_keys(p.read_text(encoding="utf-8"))
    return PriorSet(
        provenance=str(raw.get("provenance", "UNKNOWN")),
        version=str(raw.get("version", "?")),
        source_path=str(p),
        fit_provenance=raw.get("fit_provenance"),
        cells=cells,
        conditions=conditions,
        citation_keys=keys,
    )


def _extract_citation_keys(text: str) -> tuple[str, ...]:
    """Collect ``[key; key2]`` citation keys appearing in comments (for the attribution panel)."""
    import re

    found: set[str] = set()
    for m in re.finditer(r"\[([a-z0-9\-]+(?:;\s*[a-z0-9\-]+)*)\]", text):
        for k in m.group(1).split(";"):
            found.add(k.strip())
    return tuple(sorted(found))


def require_fitted(priors: PriorSet, what: str = "this computation") -> None:
    """Refuse PLACEHOLDER priors for validation-grade work (CLAUDE.md Phase 1 / Phase 6)."""
    if priors.is_placeholder:
        raise PlaceholderPriorsError(
            f"{what} requires fitted priors, but {priors.source_path} has provenance PLACEHOLDER"
        )


def perturb_priors(priors: PriorSet, alpha: float = 0.0, mu: float = 0.0, nu_on: float = 0.0, nu_off: float = 0.0) -> PriorSet:
    """Return a copy with additive perturbations applied to every cell (TDD section 6.2 critique; V5).

    ``alpha`` shifts the Pareto tail index; ``mu`` shifts both mu_0 and mu_1 (log rate); ``nu_on`` / ``nu_off``
    shift the duration location parameters. The result keeps the source provenance but records the perturbation.
    """
    from dataclasses import replace

    cells = {k: replace(v, alpha=max(v.alpha + alpha, 0.2), mu_0=v.mu_0 + mu, mu_1=v.mu_1 + mu, nu_on=v.nu_on + nu_on, nu_off=v.nu_off + nu_off)
             for k, v in priors.cells.items()}
    return PriorSet(provenance=priors.provenance, version=f"{priors.version}+perturbed", source_path=priors.source_path,
                    fit_provenance={"perturbation": {"alpha": alpha, "mu": mu, "nu_on": nu_on, "nu_off": nu_off}, "base": priors.fit_provenance},
                    cells=cells, conditions=priors.conditions, citation_keys=priors.citation_keys)
