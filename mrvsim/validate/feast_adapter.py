"""FEAST 3.1 adapter for validation test V2 (TDD section 9) [kemp2016; feast-repo].

Converts an MRVSim population into a FEAST gas field and runs FEAST with no LDAR
program, returning the annual emitted mass FEAST simulates for the same sources:

* steady sources (z = 0) enter as a pre-built FEAST ``Emission`` object with
  constant flux from t = 0 to infinity (FEAST integrates them; no modelling
  freedom);
* intermittent sources (z = 1) enter through FEAST's own episodic-emission
  machinery: one component per source with ``episodic_emission_sizes = [q]``,
  ``episodic_emission_duration = E[D_on]`` (days) and
  ``episodic_emission_per_day = 1 / (E[D_on] + E[D_off])``. FEAST draws Poisson
  event starts and fixed durations, so the comparison tests whether MRVSim's
  alternating renewal process (lognormal durations, stationary start) and
  FEAST's episodic model agree on annual mass for identical duty cycles.

FEAST 3.1 targets numpy < 1.24 (``np.bool``, ``np.infty``, ``np.math``); the shim
in :func:`import_feast` restores those aliases before import. It only touches the
numpy namespace while FEAST is imported and is recorded in DECISION_LOG (Phase 6).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

from mrvsim.population.generate import Population
from mrvsim.population.temporal import lognormal_mean

FEAST_DIR = Path(__file__).resolve().parents[2] / "vendor" / "feast"
G_PER_S_PER_KG_PER_H = 1000.0 / 3600.0


def feast_available() -> bool:
    return (FEAST_DIR / "feast" / "__init__.py").exists()


def import_feast() -> dict[str, Any]:
    if not feast_available():
        raise ImportError(f"FEAST 3.1 not found at {FEAST_DIR}; run `git submodule update --init vendor/feast`")
    for name, val in (("bool", np.bool_), ("infty", np.inf), ("math", math), ("float", float), ("int", int)):
        if name not in np.__dict__:
            setattr(np, name, val)
    if str(FEAST_DIR) not in sys.path:
        sys.path.insert(0, str(FEAST_DIR))
    import feast.EmissionSimModules.emission_class_functions as ecf
    import feast.EmissionSimModules.infrastructure_classes as ic
    import feast.EmissionSimModules.simulation_classes as sc

    # pandas >= 3 (copy-on-write) hands FEAST read-only arrays that it mutates in place; same arithmetic, on copies.
    def _em_rate_in_range(self, t0, t1, reparable=None):  # noqa: ANN001
        em = self.get_emissions_in_range(t0, t1, reparable=reparable)
        st = np.array(em["start_time"].to_numpy(), dtype=float, copy=True)
        et = np.array(em["end_time"].to_numpy(), dtype=float, copy=True)
        st[st < t0] = t0
        et[et > t1] = t1
        return float(np.sum(em["flux"].to_numpy() * (et - st)) / (t1 - t0))

    ecf.Emission.em_rate_in_range = _em_rate_in_range
    return {"ic": ic, "sc": sc, "ecf": ecf}


def build_gas_field(pop: Population, facilities: np.ndarray, mods: dict[str, Any], end_time_days: int = 365):
    ic, sc, ecf = mods["ic"], mods["sc"], mods["ecf"]
    time = sc.Time(delta_t=1, end_time=end_time_days)
    site_dict: dict[str, dict[str, Any]] = {}
    steady_flux, steady_site = [], []
    for site_idx, f in enumerate(facilities):
        s0, s1 = pop.source_offset[f], pop.source_offset[f + 1]
        comp_dict: dict[str, Any] = {}
        for j in range(s0, s1):
            if pop.z[j] == 1:
                e_on_d = float(lognormal_mean(pop.nu_on[j], pop.tau_on[j])) / 24.0
                e_off_d = float(lognormal_mean(pop.nu_off[j], pop.tau_off[j])) / 24.0
                comp_dict[f"int_{j}"] = {"number": 1, "parameters": ic.Component(
                    name=f"int_{j}", emission_production_rate=0, episodic_emission_sizes=[float(pop.q_kg_h[j]) * G_PER_S_PER_KG_PER_H],
                    episodic_emission_duration=e_on_d, episodic_emission_per_day=1.0 / (e_on_d + e_off_d))}
            else:
                steady_flux.append(float(pop.q_kg_h[j]) * G_PER_S_PER_KG_PER_H); steady_site.append(site_idx)
        if not comp_dict:   # FEAST needs at least one component per site
            comp_dict[f"none_{f}"] = {"number": 1, "parameters": ic.Component(name=f"none_{f}", emission_production_rate=0)}
        site_dict[f"fac_{f}"] = {"number": 1, "parameters": ic.Site(name=f"fac_{f}", comp_dict=comp_dict)}
    gas_field = ic.GasField(time=time, sites=site_dict)
    if steady_flux:
        n = len(steady_flux)
        existing = gas_field.emissions.emissions.index
        first_id = int(existing.max()) + 1 if len(existing) else 0
        steady = ecf.Emission(flux=np.array(steady_flux), reparable=False, site_index=np.array(steady_site), comp_index=np.zeros(n, dtype=int),
                              start_time=np.zeros(n), end_time=np.full(n, np.inf), repair_cost=np.zeros(n),
                              emission_id=first_id + np.arange(n))
        gas_field.emissions.extend(steady)
    return time, gas_field


def feast_annual_mass_kg(pop: Population, facilities: np.ndarray | None = None, seed: int = 0, end_time_days: int = 365) -> dict[str, Any]:
    """Run FEAST (null LDAR program) on the population and return annual mass totals."""
    mods = import_feast()
    facilities = np.arange(pop.n_facilities) if facilities is None else np.asarray(facilities)
    np.random.seed(seed)   # FEAST uses the global numpy RNG
    time, gas_field = build_gas_field(pop, facilities, mods, end_time_days)
    scenario = mods["sc"].Scenario(time, gas_field, {})
    progs = scenario.run(display_status=False, save_method="object")
    ts = np.asarray(progs["Null"].emissions_timeseries, dtype=float)      # g/s per time step
    total_kg = float(ts.sum() * time.delta_t * 86400.0 / 1000.0)
    sel = facilities
    src = np.concatenate([np.arange(pop.source_offset[f], pop.source_offset[f + 1]) for f in sel])
    expected = float((pop.pi[src] * pop.q_kg_h[src] * pop.n_hours).sum())
    realised = float((pop.q_kg_h[src] * pop.states.on_hours()[src]).sum())
    return {"feast_total_kg": total_kg, "mrvsim_expected_kg": expected, "mrvsim_realised_kg": realised,
            "ratio_feast_over_expected": total_kg / expected if expected > 0 else float("nan"),
            "ratio_feast_over_realised": total_kg / realised if realised > 0 else float("nan"),
            "n_facilities": int(sel.size), "n_sources": int(src.size), "n_timesteps": int(ts.size)}
