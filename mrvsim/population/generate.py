"""Population generator entry point (TDD section 3): strata -> facilities -> sources -> states.

:func:`generate_population` is a pure function of (config, priors, seed tree).
It returns a :class:`Population` whose arrays are flattened over facilities and
over sources (CSR layout: ``source_offset[i]:source_offset[i+1]`` are facility
``i``'s sources) so that downstream stages vectorise over the whole sample.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from mrvsim.io.seeds import SeedTree
from mrvsim.population.conditions import Conditions, draw_conditions
from mrvsim.population.priors import DEFAULT_PRIORS_PATH, PriorSet, load_priors
from mrvsim.population.sources import draw_rates_kg_h, draw_source_counts, draw_source_types, rate_params_for_type
from mrvsim.population.strata import DEFAULT_STRATA_PATH, StrataTable, load_strata
from mrvsim.population.temporal import StatePaths, duty_cycle, simulate_intermittent_states
from mrvsim.population.throughput import Constants, Throughput, draw_throughput

_REPO = Path(__file__).resolve().parents[2]


@dataclass
class Population:
    """Synthetic facilities with known ("true") sources and states."""

    # facility-level (n_fac)
    stratum_idx: np.ndarray
    basin_idx: np.ndarray
    ftype_idx: np.ndarray
    tclass_idx: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    n_sources: np.ndarray            # K_i
    source_offset: np.ndarray        # (n_fac + 1) CSR offsets into source arrays
    throughput: Throughput
    conditions: Conditions
    # source-level (n_src)
    src_facility: np.ndarray         # facility index per source
    z: np.ndarray                    # 0 steady, 1 intermittent
    q_kg_h: np.ndarray               # rate when on
    pi: np.ndarray                   # duty cycle (1 for steady)
    nu_on: np.ndarray
    tau_on: np.ndarray
    nu_off: np.ndarray
    tau_off: np.ndarray
    states: StatePaths
    # metadata
    strata: StrataTable
    priors: PriorSet
    constants: Constants
    n_hours: int = 8760
    meta: dict[str, Any] = field(default_factory=dict)

    # -- derived truth ------------------------------------------------------
    @property
    def n_facilities(self) -> int:
        return int(self.n_sources.shape[0])

    @property
    def n_total_sources(self) -> int:
        return int(self.q_kg_h.shape[0])

    def source_mass_kg_yr(self) -> np.ndarray:
        """Per-source true annual mass from the realised state path."""
        return self.q_kg_h * self.states.on_hours()

    def true_mass_kg_yr(self) -> np.ndarray:
        """M_i = sum_t Q_i(t) (TDD section 2), from realised states."""
        return np.bincount(self.src_facility, weights=self.source_mass_kg_yr(), minlength=self.n_facilities)

    def expected_mass_kg_yr(self) -> np.ndarray:
        """sum_j pi_j q_j T (PRD section 5.2), the process expectation rather than the realisation."""
        return np.bincount(self.src_facility, weights=self.pi * self.q_kg_h * self.n_hours, minlength=self.n_facilities)

    def true_intensity(self) -> np.ndarray:
        """I_i = f_gas M_i / G_i (PRD section 5.1; TDD section 6.7)."""
        return self.throughput.f_gas * self.true_mass_kg_yr() / self.throughput.g_ch4_kg_yr

    def facility_rate_at(self, hours: np.ndarray) -> np.ndarray:
        """Q_i(t) for the given hour indices: shape (n_fac, len(hours)) kg/h."""
        hours = np.asarray(hours, dtype=np.int64)
        on = self.states.state_at(hours)                             # (n_src, H)
        rates = on * self.q_kg_h[:, None]
        out = np.zeros((self.n_facilities, hours.shape[0]))
        np.add.at(out, self.src_facility, rates)
        return out

    def facility_rate_at_pairs(self, facility_idx: np.ndarray, hours: np.ndarray) -> np.ndarray:
        """Q_i(t) for paired (facility, hour) queries, vectorised over pairs."""
        facility_idx = np.asarray(facility_idx, dtype=np.int64)
        hours = np.asarray(hours, dtype=np.int64)
        start = self.source_offset[facility_idx]
        count = self.n_sources[facility_idx]
        # Expand pairs to (pair, source) with a ragged->flat trick.
        rep = np.repeat(np.arange(facility_idx.shape[0]), count)
        src = np.concatenate([np.arange(s, s + c) for s, c in zip(start, count)]) if start.size else np.array([], dtype=np.int64)
        on = self.states.state_at_pairs(src, hours[rep])
        return np.bincount(rep, weights=on * self.q_kg_h[src], minlength=facility_idx.shape[0])

    def stratum_weights(self, kind: str = "count") -> np.ndarray:
        """Per-facility weight W_h / n_h for aggregates (TDD section 3.1)."""
        w = self.strata.weights_count() if kind == "count" else self.strata.weights_throughput()
        n_h = np.bincount(self.stratum_idx, minlength=len(self.strata))
        return w[self.stratum_idx] / n_h[self.stratum_idx]

    def citation_keys(self) -> tuple[str, ...]:
        keys = set(self.priors.citation_keys) | {"ngsi", "jacob2022", "eia-heat-content", "basin-extents", "renewal-theory"}
        return tuple(sorted(keys))

    # -- persistence --------------------------------------------------------
    def save(self, directory: Path) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            directory / "facilities.npz",
            stratum_idx=self.stratum_idx, basin_idx=self.basin_idx, ftype_idx=self.ftype_idx, tclass_idx=self.tclass_idx,
            lat=self.lat, lon=self.lon, n_sources=self.n_sources, source_offset=self.source_offset,
            gas_mkt_m3_yr=self.throughput.gas_mkt_m3_yr, oil_bbl_yr=self.throughput.oil_bbl_yr, x_ch4=self.throughput.x_ch4,
            g_ch4_kg_yr=self.throughput.g_ch4_kg_yr, f_gas=self.throughput.f_gas, mmbtu_yr=self.throughput.mmbtu_yr,
            ghgrp_reporter=self.throughput.ghgrp_reporter,
            p_cloud=self.conditions.p_cloud, surface_reflectance=self.conditions.surface_reflectance,
            surface_heterogeneity=self.conditions.surface_heterogeneity, wind_k=self.conditions.wind_k,
            wind_lambda=self.conditions.wind_lambda,
        )
        np.savez_compressed(
            directory / "sources.npz",
            src_facility=self.src_facility, z=self.z, q_kg_h=self.q_kg_h, pi=self.pi,
            nu_on=self.nu_on, tau_on=self.tau_on, nu_off=self.nu_off, tau_off=self.tau_off,
        )
        self.states.save(directory / "states.npz")


def _facility_coordinates(rng: np.random.Generator, strata: StrataTable, basin_idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Uniform placement inside each basin's box [basin-extents] (representative, not real assets)."""
    keys = list(strata.basins)
    lat_lo = np.array([strata.basins[k].lat_range[0] for k in keys])[basin_idx]
    lat_hi = np.array([strata.basins[k].lat_range[1] for k in keys])[basin_idx]
    lon_lo = np.array([strata.basins[k].lon_range[0] for k in keys])[basin_idx]
    lon_hi = np.array([strata.basins[k].lon_range[1] for k in keys])[basin_idx]
    u = rng.random((2, basin_idx.shape[0]))
    return lat_lo + u[0] * (lat_hi - lat_lo), lon_lo + u[1] * (lon_hi - lon_lo)


def generate_population(
    population_cfg: Mapping[str, Any],
    seeds: SeedTree,
    strata: StrataTable | None = None,
    priors: PriorSet | None = None,
    constants: Constants | None = None,
    n_hours: int = 8760,
) -> Population:
    """Generate the stratified synthetic population (TDD section 3).

    Parameters
    ----------
    population_cfg:
        The ``population`` section of the run config: ``n_per_stratum``
        (default 100), ``min_per_stratum`` (30), ``strata_path``,
        ``priors_path``, ``n_throughput_classes`` (3; 5 for V4).
    seeds:
        Seed tree scoped to this population (e.g. ``run.seeds.child(rep=r)``).
        Streams used: ``population/source_count``, ``population/source_type``,
        ``population/rates``, ``population/durations``, ``population/states``,
        ``population/throughput``, ``population/conditions``, ``population/coordinates``.
    """
    n_per = int(population_cfg.get("n_per_stratum", 100))
    n_min = int(population_cfg.get("min_per_stratum", 30))
    n_classes = population_cfg.get("n_throughput_classes")
    strata = strata or load_strata(_resolve(population_cfg.get("strata_path"), DEFAULT_STRATA_PATH), n_classes=n_classes)
    priors = priors or load_priors(
        _resolve(population_cfg.get("priors_path"), DEFAULT_PRIORS_PATH), list(strata.basins), list(strata.facility_types)
    )
    constants = constants or Constants.load()

    # --- facilities ---------------------------------------------------------
    sizes = strata.sizes(n_per, n_min)
    stratum_idx = np.repeat(np.arange(len(strata)), sizes)
    n_fac = stratum_idx.shape[0]
    b_index, f_index = strata.basin_index(), strata.facility_type_index()
    basin_of_stratum = np.array([b_index[s.basin] for s in strata.strata])
    ftype_of_stratum = np.array([f_index[s.facility_type] for s in strata.strata])
    tclass_of_stratum = np.array([s.throughput_class_index for s in strata.strata])
    basin_idx = basin_of_stratum[stratum_idx]
    ftype_idx = ftype_of_stratum[stratum_idx]
    tclass_idx = tclass_of_stratum[stratum_idx]
    lat, lon = _facility_coordinates(seeds.rng("population", "coordinates"), strata, basin_idx)

    # per-stratum hyperparameters as arrays indexed by stratum
    cell_priors = [priors.for_cell(s.basin, s.facility_type) for s in strata.strata]
    P = {k: np.array([getattr(cp, k) for cp in cell_priors]) for k in (
        "lambda_k", "p_intermittent", "mu_0", "sigma_0", "mu_1", "sigma_1", "q_tail", "alpha",
        "nu_on", "tau_on", "nu_off", "tau_off",
        "ln_gas_m3_yr_mu", "ln_gas_m3_yr_sigma", "ln_oil_bbl_yr_mu", "ln_oil_bbl_yr_sigma",
    )}
    ghgrp_share = np.array([strata.facility_types[k].ghgrp_reporter_share for k in strata.facility_types])[ftype_idx]

    # --- sources ------------------------------------------------------------
    K = draw_source_counts(seeds.rng("population", "source_count"), P["lambda_k"][stratum_idx])
    source_offset = np.concatenate([[0], np.cumsum(K)])
    src_facility = np.repeat(np.arange(n_fac), K)
    src_stratum = stratum_idx[src_facility]
    z = draw_source_types(seeds.rng("population", "source_type"), P["p_intermittent"][src_stratum])
    mu, sigma = rate_params_for_type(z, P["mu_0"][src_stratum], P["sigma_0"][src_stratum], P["mu_1"][src_stratum], P["sigma_1"][src_stratum])
    q = draw_rates_kg_h(seeds.rng("population", "rates"), mu, sigma, P["q_tail"][src_stratum], P["alpha"][src_stratum])

    nu_on, tau_on = P["nu_on"][src_stratum], P["tau_on"][src_stratum]
    nu_off, tau_off = P["nu_off"][src_stratum], P["tau_off"][src_stratum]
    pi = np.where(z == 1, duty_cycle(nu_on, tau_on, nu_off, tau_off), 1.0)

    # --- states -------------------------------------------------------------
    states_bool = np.ones((src_facility.shape[0], n_hours), dtype=bool)
    inter = np.nonzero(z == 1)[0]
    if inter.size:
        states_bool[inter] = simulate_intermittent_states(
            seeds.rng("population", "states"), nu_on[inter], tau_on[inter], nu_off[inter], tau_off[inter], n_hours
        )
    states = StatePaths.from_bool(states_bool)
    del states_bool

    # --- throughput and conditions -------------------------------------------
    n_cls = np.array([s.n_classes for s in strata.strata])[stratum_idx]
    throughput = draw_throughput(
        seeds.rng("population", "throughput"),
        P["ln_gas_m3_yr_mu"][stratum_idx], P["ln_gas_m3_yr_sigma"][stratum_idx],
        P["ln_oil_bbl_yr_mu"][stratum_idx], P["ln_oil_bbl_yr_sigma"][stratum_idx],
        tclass_idx, n_cls, ghgrp_share, constants,
    )
    conditions = draw_conditions(seeds.rng("population", "conditions"), list(strata.basins), basin_idx, priors)

    return Population(
        stratum_idx=stratum_idx, basin_idx=basin_idx, ftype_idx=ftype_idx, tclass_idx=tclass_idx, lat=lat, lon=lon,
        n_sources=K, source_offset=source_offset, throughput=throughput, conditions=conditions,
        src_facility=src_facility, z=z, q_kg_h=q, pi=pi, nu_on=nu_on, tau_on=tau_on, nu_off=nu_off, tau_off=tau_off,
        states=states, strata=strata, priors=priors, constants=constants, n_hours=n_hours,
        meta={"n_per_stratum": n_per, "n_strata": len(strata), "priors_provenance": priors.provenance,
              "strata_provenance": strata.provenance, "seed_tree": seeds.describe()},
    )


def _resolve(path: str | None, default: Path) -> Path:
    if path is None:
        return default
    p = Path(path)
    return p if p.is_absolute() else _REPO / p
