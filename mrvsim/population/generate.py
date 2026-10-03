"""Population generator entry point (TDD section 3): strata -> facilities -> sources -> states.

:func:`generate_population` is a pure function of (config, priors, seed tree).
It returns a :class:`Population` whose arrays are flattened over facilities and
over sources (CSR layout: ``source_offset[i]:source_offset[i+1]`` are facility
``i``'s sources) so that downstream stages vectorise over the whole sample.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from mrvsim.io.seeds import SeedTree
from mrvsim.population.conditions import Conditions, draw_conditions
from mrvsim.population.priors import DEFAULT_PRIORS_PATH, PriorSet, load_priors
from mrvsim.population.sources import draw_rates_kg_h, draw_source_counts, draw_source_types, rate_params_for_type
from mrvsim.population.sites import DEFAULT_SITES_PATH
from mrvsim.population.strata import DEFAULT_STRATA_PATH, StrataTable, load_saved_strata, load_strata
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
    site_row: np.ndarray | None = None   # row of strata.sites behind each facility, -1 if none [ogim]
    sites_represented: np.ndarray | None = None   # real sites each facility stands for, N_h / n_h (NaN if not a real site) [ogim]

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
        """Per-facility weight for aggregates (TDD section 3.1): W_h / n_h.

        For throughput aggregates in a stratum of real sites [ogim], the stratum's weight is shared in
        proportion to each sampled facility's own gas volume rather than equally (TDD section 3.1 as
        amended 2026-10-03): throughput varies by orders of magnitude inside a class of real sites.
        """
        w = self.strata.weights_count() if kind == "count" else self.strata.weights_throughput()
        n_h = np.bincount(self.stratum_idx, minlength=len(self.strata))
        out = w[self.stratum_idx] / n_h[self.stratum_idx]
        if kind != "count" and self.site_row is not None:
            real = self.site_row >= 0
            gas = np.where(real, self.throughput.gas_mkt_m3_yr, 0.0)
            gas_h = np.bincount(self.stratum_idx, weights=gas, minlength=len(self.strata))
            share = real & (gas_h[self.stratum_idx] > 0)
            out = np.where(share, w[self.stratum_idx] * gas / np.where(gas_h > 0, gas_h, 1.0)[self.stratum_idx], out)
        return out

    def citation_keys(self) -> tuple[str, ...]:
        keys = set(self.priors.citation_keys) | {"ngsi", "jacob2022", "eia-heat-content", "basin-extents", "renewal-theory"}
        if self.site_row is not None and (self.site_row >= 0).any():
            keys.add("ogim")
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
            site_row=self.site_row if self.site_row is not None else np.full(self.stratum_idx.shape[0], -1, dtype=np.int64),
            sites_represented=self.sites_represented if self.sites_represented is not None else np.full(self.stratum_idx.shape[0], np.nan),
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
        # The strata table as used, so that a saved run does not depend on the current configs.
        (directory / "strata.json").write_text(json.dumps({
            "provenance": self.strata.provenance, "throughput_classes": list(self.strata.throughput_classes), "class_rule": self.strata.class_rule,
            "strata": [{"basin": t.basin, "facility_type": t.facility_type, "throughput_class": t.throughput_class,
                        "throughput_class_index": t.throughput_class_index, "n_classes": t.n_classes,
                        "weight_count": t.weight_count, "weight_throughput": t.weight_throughput} for t in self.strata.strata],
        }, indent=1) + "\n", encoding="utf-8")


def load_population(directory: Path, strata: StrataTable | None = None, priors: PriorSet | None = None,
                    constants: Constants | None = None, n_hours: int = 8760) -> Population:
    """Inverse of :meth:`Population.save` (strata/priors/constants reloaded from configs unless given)."""
    directory = Path(directory)
    strata = strata or load_saved_strata(directory)
    priors = priors or load_priors(DEFAULT_PRIORS_PATH, list(strata.basins), list(strata.facility_types))
    constants = constants or Constants.load()
    with np.load(directory / "facilities.npz") as f:
        F = {k: f[k] for k in f.files}
    with np.load(directory / "sources.npz") as s:
        S = {k: s[k] for k in s.files}
    states = StatePaths.load(directory / "states.npz")
    thr = Throughput(F["gas_mkt_m3_yr"], F["oil_bbl_yr"], F["x_ch4"], F["g_ch4_kg_yr"], F["f_gas"], F["mmbtu_yr"], F["ghgrp_reporter"])
    cond = Conditions(F["p_cloud"], F["surface_reflectance"], F["surface_heterogeneity"], F["wind_k"], F["wind_lambda"])
    return Population(stratum_idx=F["stratum_idx"], basin_idx=F["basin_idx"], ftype_idx=F["ftype_idx"], tclass_idx=F["tclass_idx"], lat=F["lat"], lon=F["lon"],
                      n_sources=F["n_sources"], source_offset=F["source_offset"], throughput=thr, conditions=cond,
                      src_facility=S["src_facility"], z=S["z"], q_kg_h=S["q_kg_h"], pi=S["pi"], nu_on=S["nu_on"], tau_on=S["tau_on"], nu_off=S["nu_off"], tau_off=S["tau_off"],
                      states=states, strata=strata, priors=priors, constants=constants, n_hours=n_hours, meta={"loaded_from": str(directory)},
                      site_row=F.get("site_row"), sites_represented=F.get("sites_represented"))


def _facility_sites(rng: np.random.Generator, strata: StrataTable, stratum_idx: np.ndarray, basin_idx: np.ndarray,
                    ftype_idx: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Facility coordinates and, where known, the real site behind each facility (TDD section 3.1 as amended 2026-10-03).

    A facility of a stratum with real sites is one of those sites, drawn without replacement: it takes the
    site's location and production [ogim]. A midstream facility takes a location from its cell's pool.
    Anything else is placed uniformly inside the basin's box [basin-extents]. Returns (lat, lon, site_row)
    with site_row = -1 where the facility is not a real site. Emissions are synthetic in every case.
    """
    keys = list(strata.basins)
    lat_lo = np.array([strata.basins[k].lat_range[0] for k in keys])[basin_idx]
    lat_hi = np.array([strata.basins[k].lat_range[1] for k in keys])[basin_idx]
    lon_lo = np.array([strata.basins[k].lon_range[0] for k in keys])[basin_idx]
    lon_hi = np.array([strata.basins[k].lon_range[1] for k in keys])[basin_idx]
    u = rng.random((2, basin_idx.shape[0]))
    lat, lon = lat_lo + u[0] * (lat_hi - lat_lo), lon_lo + u[1] * (lon_hi - lon_lo)
    site_row = np.full(basin_idx.shape[0], -1, dtype=np.int64)
    sites = strata.sites
    if sites is None:
        return lat, lon, site_row
    ftypes = list(strata.facility_types)
    for h in range(len(strata)):
        fac = np.nonzero(stratum_idx == h)[0]
        rows = strata.rows_of(h)
        if rows is not None:
            pick = rows[rng.choice(rows.shape[0], size=fac.shape[0], replace=False)]
            lat[fac], lon[fac], site_row[fac] = sites.lat[pick], sites.lon[pick], pick
    for b, f in sorted({(int(b), int(f)) for b, f in zip(basin_idx[site_row < 0], ftype_idx[site_row < 0])}):
        pool = sites.pools.get(f"{keys[b]}/{ftypes[f]}")
        if pool is None or len(pool) == 0:
            continue
        fac = np.nonzero((basin_idx == b) & (ftype_idx == f) & (site_row < 0))[0]
        pick = rng.choice(len(pool), size=fac.shape[0], replace=len(pool) < fac.shape[0])
        lat[fac], lon[fac] = pool[pick, 0], pool[pick, 1]
    return lat, lon, site_row


def generate_population(
    population_cfg: Mapping[str, Any],
    seeds: SeedTree,
    strata: StrataTable | None = None,
    priors: PriorSet | None = None,
    constants: Constants | None = None,
    n_hours: int = 8760,
    coordinate_seeds: SeedTree | None = None,
) -> Population:
    """Generate the stratified synthetic population (TDD section 3).

    ``coordinate_seeds`` (default: ``seeds``) draws the facility coordinates; the pipeline passes the
    run-level tree so that locations are identical across Monte Carlo replications while emissions vary
    (DECISION_LOG 2026-10-01). Identical coordinates let the satellite overpass cache hit after the first replication.

    Parameters
    ----------
    population_cfg:
        The ``population`` section of the run config: ``n_per_stratum``
        (default 100), ``min_per_stratum`` (30), ``strata_path``,
        ``priors_path``, ``n_throughput_classes`` (3; 5 for V4), ``site_locations``
        (``ogim``: well-pad facilities are real production sites with their real production, and
        midstream facilities sit on real locations [ogim], the default; ``box``: uniform in the
        basin boxes with lognormal throughput), ``sites_path``, ``throughput_class_rule``
        (``count`` or ``throughput``; see :func:`mrvsim.population.strata.load_strata`).
    seeds:
        Seed tree scoped to this population (e.g. ``run.seeds.child(rep=r)``).
        Streams used: ``population/source_count``, ``population/source_type``,
        ``population/rates``, ``population/durations``, ``population/states``,
        ``population/throughput``, ``population/conditions``, ``population/coordinates``.
    """
    n_per = int(population_cfg.get("n_per_stratum", 100))
    n_min = int(population_cfg.get("min_per_stratum", 30))
    n_classes = population_cfg.get("n_throughput_classes")
    use_sites = population_cfg.get("site_locations", "ogim") != "box"
    strata = strata or load_strata(
        _resolve(population_cfg.get("strata_path"), DEFAULT_STRATA_PATH), n_classes=n_classes,
        sites=_resolve(population_cfg.get("sites_path"), DEFAULT_SITES_PATH) if use_sites else None,
        class_rule=str(population_cfg.get("throughput_class_rule", "count")),
    )
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
    lat, lon, site_row = _facility_sites((coordinate_seeds or seeds).rng("population", "coordinates"), strata, stratum_idx, basin_idx, ftype_idx)

    n_sites_h = np.array([np.nan if strata.rows_of(h) is None else strata.rows_of(h).shape[0] for h in range(len(strata))])
    sites_represented = (n_sites_h / sizes)[stratum_idx]

    # per-stratum hyperparameters as arrays indexed by stratum
    cell_priors = [priors.for_cell(s.basin, s.facility_type) for s in strata.strata]
    P = {k: np.array([getattr(cp, k) for cp in cell_priors]) for k in (
        "lambda_k", "p_intermittent", "mu_0", "sigma_0", "mu_1", "sigma_1", "q_tail", "alpha",
        "nu_on", "tau_on", "nu_off", "tau_off", "sigma_nu_on", "sigma_nu_off",
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

    # Per-source duration location parameters: stratum value plus between-source spread, so that duty
    # cycles vary across sources (DECISION_LOG 2026-09-30, Phase 4). tau (within-source spread) stays per stratum.
    rng_dur = seeds.rng("population", "durations")
    nu_on = P["nu_on"][src_stratum] + rng_dur.normal(0.0, 1.0, size=src_stratum.shape) * P["sigma_nu_on"][src_stratum]
    nu_off = P["nu_off"][src_stratum] + rng_dur.normal(0.0, 1.0, size=src_stratum.shape) * P["sigma_nu_off"][src_stratum]
    tau_on, tau_off = P["tau_on"][src_stratum], P["tau_off"][src_stratum]
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
        gas_m3_yr=np.where(site_row >= 0, strata.sites.gas_m3_yr[site_row], np.nan) if strata.sites is not None and len(strata.sites) else None,
        oil_bbl_yr=np.where(site_row >= 0, strata.sites.oil_bbl_yr[site_row], np.nan) if strata.sites is not None and len(strata.sites) else None,
    )
    conditions = draw_conditions(seeds.rng("population", "conditions"), list(strata.basins), basin_idx, priors)

    return Population(
        stratum_idx=stratum_idx, basin_idx=basin_idx, ftype_idx=ftype_idx, tclass_idx=tclass_idx, lat=lat, lon=lon,
        n_sources=K, source_offset=source_offset, throughput=throughput, conditions=conditions,
        src_facility=src_facility, z=z, q_kg_h=q, pi=pi, nu_on=nu_on, tau_on=tau_on, nu_off=nu_off, tau_off=tau_off,
        states=states, strata=strata, priors=priors, constants=constants, n_hours=n_hours, site_row=site_row,
        sites_represented=sites_represented,
        meta={"n_per_stratum": n_per, "n_strata": len(strata), "priors_provenance": priors.provenance,
              "strata_provenance": strata.provenance, "seed_tree": seeds.describe()},
    )


def _resolve(path: str | None, default: Path) -> Path:
    if path is None:
        return default
    p = Path(path)
    return p if p.is_absolute() else _REPO / p
