"""Shared helpers for the validation modules."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from mrvsim.io.config import RunConfig
from mrvsim.io.seeds import SeedTree
from mrvsim.population import generate_population, load_priors, load_strata
from mrvsim.population.priors import DEFAULT_PRIORS_PATH, PriorSet
from mrvsim.validate.results import ValidationResult, placeholder_allowed, refusal


def default_priors() -> PriorSet:
    st = load_strata()
    return load_priors(DEFAULT_PRIORS_PATH, list(st.basins), list(st.facility_types))


def guard_placeholder(vid: str, name: str, rule: str, cites: list[str], priors: PriorSet | None = None) -> ValidationResult | None:
    """Return a refusal result when priors are PLACEHOLDER and the override is not set (CLAUDE.md Phase 6)."""
    priors = priors or default_priors()
    if priors.is_placeholder and not placeholder_allowed():
        return refusal(vid, name, rule, cites, f"refused: priors have provenance PLACEHOLDER ({priors.source_path}); "
                                               "set MRVSIM_ALLOW_PLACEHOLDER_VALIDATION=1 for a diagnostic-only run")
    return None


def finish(res: ValidationResult, t0: float, priors: PriorSet | None = None) -> ValidationResult:
    res.elapsed_s = round(time.perf_counter() - t0, 1)
    if priors is not None and priors.is_placeholder:
        res.diagnostic_only = True
        res.reason = (res.reason + " | " if res.reason else "") + "DIAGNOSTIC ONLY: PLACEHOLDER priors; not a validation against published data"
    return res


def base_config(**over: Any) -> RunConfig:
    d: dict[str, Any] = {"name": "validation", "seed": 20260930, "year": 2024, "replications": 1,
                         "population": {"n_per_stratum": 30}, "estimator": {"n_draws": 3000}, "policy": {"sensors": {}},
                         "scoring": {"bar_mass_t_yr": 50.0, "bar_intensity": 0.002, "w_max": 0.30}}
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(d.get(k), dict):
            d[k].update(v)
        else:
            d[k] = v
    return RunConfig.from_dict(d)


def weighted_basin_total_t_h(pop, mass_kg_yr: np.ndarray, basin_key: str, scale_to_national: float | None = None) -> float:
    """Stratum-weighted basin total in t/h from per-facility annual masses (sample-weighted; national if a count is given)."""
    bi = list(pop.strata.basins).index(basin_key)
    sel = pop.basin_idx == bi
    w = pop.stratum_weights("count")[sel]
    total_kg_yr = (w * mass_kg_yr[sel]).sum() / w.sum() * sel.sum() if scale_to_national is None else (w * mass_kg_yr[sel]).sum() * scale_to_national
    return float(total_kg_yr / 1000.0 / 8760.0)


def basin_loss_rate(pop, mass_kg_yr: np.ndarray, basin_key: str) -> tuple[float, str]:
    """Basin loss rate: emitted methane over marketed methane, and the estimator used.

    With the real site table [ogim] the rate is for well pads: the count-weighted mean site emission expanded to
    the basin's sites, over the basin's gas from the full table (the sampled gas is far noisier than the sampled
    emissions). Without it: count-weighted sample emissions over count-weighted sample gas, all facility types.
    Throughput weights must not be used here: they weight each facility by its own gas, which would weight
    emissions by gas as well.
    """
    bi = list(pop.strata.basins).index(basin_key)
    wc = pop.stratum_weights("count")
    sites = pop.strata.sites
    if sites is not None and len(sites) and pop.site_row is not None:
        sel = (pop.basin_idx == bi) & (pop.site_row >= 0)
        in_basin = np.array([k.split("/")[0] == basin_key for k in sites.cell_keys])[sites.cell_idx]
        c = pop.constants
        g_basin = float(sites.gas_m3_yr[in_basin].sum()) * c.rho_ch4_kg_per_m3 * c.x_ch4_default
        if sel.any() and g_basin > 0:
            mean_mass = float((wc[sel] * mass_kg_yr[sel]).sum() / wc[sel].sum())
            return mean_mass * int(in_basin.sum()) / g_basin, "well pads, expanded to the basin's sites over the basin's gas [ogim]"
    sel = pop.basin_idx == bi
    return float((wc[sel] * mass_kg_yr[sel]).sum() / (wc[sel] * pop.throughput.g_ch4_kg_yr[sel]).sum()), "count-weighted sample ratio, all facility types"

