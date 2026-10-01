"""Observation simulator (TDD section 5): opportunities -> gates -> detection -> log.

Everything is vectorised over (facility, opportunity) pairs. The estimator
receives an :class:`ObservationLog` (snapshot and survey rows) plus a
:class:`CMSLog` (dense hourly series); truth used only for scoring lives in
the separate ``oracle`` arrays and is never read by ``mrvsim.estimate``.

Gate conventions (TDD section 5.2), as implemented:

* Cloud, satellites: usable with probability 1 - p_cloud[i, m].
* Cloud, aircraft: unusable with probability p_cloud[i, m] * (1 - max_cloud_fraction_s);
  ``max_cloud_fraction`` in the sensor YAML therefore acts as "tolerance to cloud"
  (1 = flies under any cloud), the "looser threshold" of the TDD.
* Solar zenith: satellites need SZA <= max_solar_zenith_deg at the pass time; day-only
  sensors otherwise need SZA < 90 at the visit hour.
* Wind: one Weibull draw per opportunity from the facility's monthly distribution;
  usable if u <= max_wind_m_s. The same draw enters the POD wind term.
* CMS: per hour, outage with probability p_outage and wind-sector miss with probability
  1 - wind_sector_coverage, each independent across hours (simplification recorded in
  DECISION_LOG, Phase 3).

Detection (TDD section 5.3): snapshot sensors see Q_i(t); surveys and CMS see each
source's S_ij(t) q_ij. Reported rates carry the sensor's quantification error. When
the observable rate is zero the false-positive rule applies and a false call reports
a rate near the sensor's detection limit (POD quantile drawn in
[0.05, reported_rate_quantile_max]). Non-detections are logged with conditions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from mrvsim.io.seeds import SeedTree
from mrvsim.observe.deployment import DeploymentPlan
from mrvsim.observe.overpass import Overpasses, overpasses_for
from mrvsim.observe.solar import hour_index_to_unix, solar_zenith_deg
from mrvsim.population.generate import Population
from mrvsim.sensors.library import SensorLibrary
from mrvsim.sensors.schema import Sensor

HOURS_PER_YEAR = 8760
_MONTH_START_HOUR = np.array([0, 744, 1416, 2160, 2880, 3624, 4344, 5088, 5832, 6552, 7296, 8016, 8760])  # non-leap year


def month_of_hour(hour_idx: np.ndarray) -> np.ndarray:
    return np.clip(np.searchsorted(_MONTH_START_HOUR, np.asarray(hour_idx), side="right") - 1, 0, 11)


@dataclass
class ObservationLog:
    """Snapshot and survey observations, one row per (opportunity, source-or-site).

    ``source_idx`` is -1 for site-level (snapshot) rows. ``usable`` is False for
    opportunities lost to cloud, sun angle, or wind; those rows have no detection
    draw but keep their conditions so the drill-down timeline can show them.
    """

    facility_idx: np.ndarray = field(default_factory=lambda: np.array([], np.int64))
    source_idx: np.ndarray = field(default_factory=lambda: np.array([], np.int64))
    sensor_idx: np.ndarray = field(default_factory=lambda: np.array([], np.int16))
    hour_idx: np.ndarray = field(default_factory=lambda: np.array([], np.int64))
    usable: np.ndarray = field(default_factory=lambda: np.array([], bool))
    cloud_blocked: np.ndarray = field(default_factory=lambda: np.array([], bool))
    sun_blocked: np.ndarray = field(default_factory=lambda: np.array([], bool))
    wind_blocked: np.ndarray = field(default_factory=lambda: np.array([], bool))
    detected: np.ndarray = field(default_factory=lambda: np.array([], bool))
    reported_kg_h: np.ndarray = field(default_factory=lambda: np.array([], np.float64))   # nan when not detected
    wind_m_s: np.ndarray = field(default_factory=lambda: np.array([], np.float32))
    rho_surf: np.ndarray = field(default_factory=lambda: np.array([], np.float32))
    solar_zenith_deg: np.ndarray = field(default_factory=lambda: np.array([], np.float32))
    # oracle (truth) columns, for scoring and the variance budget only
    oracle_true_rate_kg_h: np.ndarray = field(default_factory=lambda: np.array([], np.float64))
    oracle_false_positive: np.ndarray = field(default_factory=lambda: np.array([], bool))
    sensor_keys: tuple[str, ...] = ()

    def __len__(self) -> int:
        return int(self.facility_idx.shape[0])

    @staticmethod
    def concat(parts: list["ObservationLog"], sensor_keys: tuple[str, ...]) -> "ObservationLog":
        parts = [p for p in parts if len(p)]
        if not parts:
            return ObservationLog(sensor_keys=sensor_keys)
        out = ObservationLog(sensor_keys=sensor_keys)
        for name in out.__dataclass_fields__:
            if name == "sensor_keys":
                continue
            setattr(out, name, np.concatenate([getattr(p, name) for p in parts]))
        return out

    def for_facility(self, i: int) -> "ObservationLog":
        m = self.facility_idx == i
        out = ObservationLog(sensor_keys=self.sensor_keys)
        for name in out.__dataclass_fields__:
            if name != "sensor_keys":
                setattr(out, name, getattr(self, name)[m])
        return out

    def save(self, path: Path) -> None:
        np.savez_compressed(path, **{n: getattr(self, n) for n in self.__dataclass_fields__ if n != "sensor_keys"},
                            sensor_keys=np.array(self.sensor_keys))

    @classmethod
    def load(cls, path: Path) -> "ObservationLog":
        with np.load(path) as z:
            out = cls(sensor_keys=tuple(z["sensor_keys"].tolist()))
            for n in out.__dataclass_fields__:
                if n != "sensor_keys":
                    setattr(out, n, z[n])
            return out


@dataclass
class CMSLog:
    """Dense hourly CMS series for instrumented facilities (TDD section 5.3, 6.4)."""

    sensor_key: str
    facilities: np.ndarray                 # (n_cms,) facility indices
    usable: np.ndarray                     # (n_cms, 8760) bool: not in outage and wind sector covered
    detected: np.ndarray                   # (n_cms, 8760) bool
    reported_kg_h: np.ndarray              # (n_cms, 8760) float32, nan when not detected
    oracle_true_rate_kg_h: np.ndarray      # (n_cms, 8760) float32

    @property
    def n_rows(self) -> int:
        return int(self.detected.shape[0] * self.detected.shape[1])

    def rows_for_facility(self, i: int) -> int:
        return int(self.detected.shape[1]) if i in set(self.facilities.tolist()) else 0

    def save(self, path: Path) -> None:
        np.savez_compressed(path, sensor_key=np.array(self.sensor_key), facilities=self.facilities, usable=np.packbits(self.usable, axis=1),
                            detected=np.packbits(self.detected, axis=1), reported_kg_h=self.reported_kg_h, oracle_true_rate_kg_h=self.oracle_true_rate_kg_h)

    @classmethod
    def load(cls, path: Path) -> "CMSLog":
        with np.load(path) as z:
            n = z["reported_kg_h"].shape[1]
            return cls(str(z["sensor_key"]), z["facilities"], np.unpackbits(z["usable"], axis=1, count=n).astype(bool),
                       np.unpackbits(z["detected"], axis=1, count=n).astype(bool), z["reported_kg_h"], z["oracle_true_rate_kg_h"])


@dataclass
class ObservationSet:
    year: int
    log: ObservationLog
    cms: dict[str, CMSLog]
    overpass_counts: dict[str, int]
    meta: dict[str, Any]

    def save(self, directory: Path) -> None:
        directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
        self.log.save(directory / "observations.npz")
        for k, c in self.cms.items():
            c.save(directory / f"cms_{k}.npz")


# --------------------------------------------------------------------------- helpers

def _false_positive_rates(rng: np.random.Generator, sensor: Sensor, n: int) -> np.ndarray:
    """False calls report a rate from the low end of the sensor's detectable range (TDD section 4.4)."""
    u = rng.uniform(0.05, max(sensor.false_positive.reported_rate_quantile_max, 0.06), size=n)
    lnq = (np.log(u / (1 - u)) - sensor.pod.a) / sensor.pod.b
    return np.exp(lnq)


def _detect_and_report(rng_det: np.random.Generator, rng_rep: np.random.Generator, sensor: Sensor, q_obs: np.ndarray,
                       wind: np.ndarray, rho: np.ndarray, no_fp: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bernoulli(POD) for q_obs > 0, false-positive rule for q_obs == 0; reported rates (TDD section 5.3)."""
    n = q_obs.shape[0]
    p = sensor.pod.prob(q_obs, wind_m_s=wind, rho_surf=rho)
    emitting = q_obs > 0
    u = rng_det.random(n)
    lam = 0.0 if no_fp else sensor.false_positive.rate_per_opportunity
    detected = np.where(emitting, u < p, u < lam)
    false_pos = detected & ~emitting
    reported = np.full(n, np.nan)
    true_det = detected & emitting
    if true_det.any():
        reported[true_det] = sensor.quantification.draw_reported(rng_rep, q_obs[true_det])
    if false_pos.any():
        reported[false_pos] = _false_positive_rates(rng_rep, sensor, int(false_pos.sum()))
    return detected, reported, false_pos


def _gates_snapshot(rng: np.random.Generator, sensor: Sensor, pop: Population, fac: np.ndarray, hour: np.ndarray,
                    sza: np.ndarray, force_usable: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Cloud, sun, wind gates for snapshot opportunities. Returns (usable, cloud_blocked, sun_blocked, wind_blocked, wind)."""
    month = month_of_hour(hour)
    p_cloud = pop.conditions.cloud_prob(fac, month)
    if sensor.sensor_class == "aircraft":
        tol = sensor.constraints.max_cloud_fraction if sensor.constraints.max_cloud_fraction is not None else 1.0
        p_cloud = p_cloud * (1.0 - tol)
    cloud_blocked = rng.random(fac.shape[0]) < p_cloud
    if sensor.constraints.max_solar_zenith_deg is not None:
        sun_blocked = sza > sensor.constraints.max_solar_zenith_deg
    elif sensor.constraints.day_only:
        sun_blocked = sza >= 90.0
    else:
        sun_blocked = np.zeros(fac.shape[0], dtype=bool)
    wind = pop.conditions.draw_wind_m_s(rng, fac, month).astype(np.float32)
    wind_blocked = (wind > sensor.constraints.max_wind_m_s) if sensor.constraints.max_wind_m_s is not None else np.zeros_like(cloud_blocked)
    if force_usable:   # oracle for the variance budget's spatial-completeness component (TDD section 6.8)
        cloud_blocked[:] = False; sun_blocked[:] = False; wind_blocked[:] = False
    usable = ~(cloud_blocked | sun_blocked | wind_blocked)
    return usable, cloud_blocked, sun_blocked, wind_blocked, wind


# --------------------------------------------------------------------------- per-sensor simulators

def simulate_snapshot(sensor: Sensor, sensor_idx: int, pop: Population, fac: np.ndarray, hour: np.ndarray,
                      sza: np.ndarray, seeds: SeedTree, force_usable: bool = False, no_fp: bool = False) -> ObservationLog:
    """Aircraft or satellite site-level snapshots at the given (facility, hour) opportunities."""
    n = fac.shape[0]
    log = ObservationLog()
    if n == 0:
        return log
    usable, cloud_b, sun_b, wind_b, wind = _gates_snapshot(seeds.rng("observe", "gates", sensor=sensor.key), sensor, pop, fac, hour, sza, force_usable)
    rho = pop.conditions.surface_reflectance[fac].astype(np.float32)
    q_true = pop.facility_rate_at_pairs(fac, hour)                       # Q_i(t), TDD section 4.3
    detected = np.zeros(n, dtype=bool); reported = np.full(n, np.nan); fp = np.zeros(n, dtype=bool)
    if usable.any():
        d, r, f = _detect_and_report(seeds.rng("observe", "detect", sensor=sensor.key), seeds.rng("observe", "report", sensor=sensor.key),
                                     sensor, q_true[usable], wind[usable], rho[usable], no_fp)
        detected[usable], reported[usable], fp[usable] = d, r, f
    log.facility_idx = fac.astype(np.int64); log.source_idx = np.full(n, -1, np.int64); log.sensor_idx = np.full(n, sensor_idx, np.int16)
    log.hour_idx = hour.astype(np.int64); log.usable = usable; log.cloud_blocked = cloud_b; log.sun_blocked = sun_b; log.wind_blocked = wind_b
    log.detected = detected; log.reported_kg_h = reported; log.wind_m_s = wind; log.rho_surf = rho
    log.solar_zenith_deg = sza.astype(np.float32); log.oracle_true_rate_kg_h = q_true; log.oracle_false_positive = fp
    return log


def simulate_survey(sensor: Sensor, sensor_idx: int, pop: Population, fac: np.ndarray, hour: np.ndarray, seeds: SeedTree, year: int,
                    force_usable: bool = False, no_fp: bool = False) -> ObservationLog:
    """Drone/OGI per-source surveys (TDD section 4.3, 5.3, 6.5). One row per (visit, candidate source)."""
    log = ObservationLog()
    if fac.shape[0] == 0:
        return log
    sza = solar_zenith_deg(hour_index_to_unix(year, hour), pop.lat[fac], pop.lon[fac]).astype(np.float32)
    usable_v, cloud_b, sun_b, wind_b, wind_v = _gates_snapshot(seeds.rng("observe", "gates", sensor=sensor.key), sensor, pop, fac, hour, sza, force_usable)
    # expand visits to sources
    counts = pop.n_sources[fac]
    rep = np.repeat(np.arange(fac.shape[0]), counts)
    src = np.concatenate([np.arange(pop.source_offset[f], pop.source_offset[f] + pop.n_sources[f]) for f in fac]).astype(np.int64)
    hours_s = hour[rep]
    on = pop.states.state_at_pairs(src, hours_s)
    q_src = on * pop.q_kg_h[src]
    n = src.shape[0]
    usable = usable_v[rep]; wind = wind_v[rep]
    rho = pop.conditions.surface_reflectance[fac][rep].astype(np.float32)
    detected = np.zeros(n, dtype=bool); reported = np.full(n, np.nan); fp = np.zeros(n, dtype=bool)
    if usable.any():
        d, r, f = _detect_and_report(seeds.rng("observe", "detect", sensor=sensor.key), seeds.rng("observe", "report", sensor=sensor.key),
                                     sensor, q_src[usable], wind[usable], rho[usable], no_fp)
        detected[usable], reported[usable], fp[usable] = d, r, f
    log.facility_idx = fac[rep].astype(np.int64); log.source_idx = src; log.sensor_idx = np.full(n, sensor_idx, np.int16)
    log.hour_idx = hours_s.astype(np.int64); log.usable = usable; log.cloud_blocked = cloud_b[rep]; log.sun_blocked = sun_b[rep]
    log.wind_blocked = wind_b[rep]; log.detected = detected; log.reported_kg_h = reported; log.wind_m_s = wind; log.rho_surf = rho
    log.solar_zenith_deg = sza[rep]; log.oracle_true_rate_kg_h = q_src; log.oracle_false_positive = fp
    return log


def simulate_cms(sensor: Sensor, pop: Population, fac: np.ndarray, seeds: SeedTree, chunk: int = 256,
                 force_usable: bool = False, no_fp: bool = False) -> CMSLog:
    """Hourly CMS series per instrumented facility (TDD section 5.2-5.3): per-source POD, OR-ed to a facility flag."""
    n_f = fac.shape[0]
    T = pop.n_hours
    usable = np.ones((n_f, T), dtype=bool); detected = np.zeros((n_f, T), dtype=bool)
    reported = np.full((n_f, T), np.nan, dtype=np.float32); truth = np.zeros((n_f, T), dtype=np.float32)
    if n_f == 0:
        return CMSLog(sensor.key, fac, usable, detected, reported, truth)
    rng_g = seeds.rng("observe", "gates", sensor=sensor.key)
    rng_d = seeds.rng("observe", "detect", sensor=sensor.key)
    rng_r = seeds.rng("observe", "report", sensor=sensor.key)
    p_out = 0.0 if force_usable else (sensor.constraints.outage_probability or 0.0)
    p_sector = 1.0 if force_usable else (sensor.constraints.wind_sector_coverage if sensor.constraints.wind_sector_coverage is not None else 1.0)
    lam_fp = 0.0 if no_fp else sensor.false_positive.rate_per_opportunity
    hours = np.arange(T)
    for lo in range(0, n_f, chunk):
        f = fac[lo:lo + chunk]; m = f.shape[0]
        usable[lo:lo + m] = (rng_g.random((m, T)) >= p_out) & (rng_g.random((m, T)) < p_sector)
        # per-source states for this chunk
        src_lists = [np.arange(pop.source_offset[i], pop.source_offset[i] + pop.n_sources[i]) for i in f]
        src = np.concatenate(src_lists); owner = np.repeat(np.arange(m), [len(s) for s in src_lists])
        on = pop.states.state_at(hours)[src]                                # (n_src_chunk, T)
        q = on * pop.q_kg_h[src][:, None]
        truth[lo:lo + m] = np.zeros((m, T), np.float32); np.add.at(truth[lo:lo + m], owner, q.astype(np.float32))
        p = sensor.pod.prob(q)                                              # gamma = 0 for CMS -> no wind term
        det_src = (rng_d.random(q.shape) < p) & (q > 0)
        # false positives per facility-hour where nothing detected
        fp = rng_d.random((m, T)) < lam_fp
        det_fac = np.zeros((m, T), dtype=bool); np.logical_or.at(det_fac, owner, det_src)
        rep_src = np.where(det_src, sensor.quantification.draw_reported(rng_r, np.where(q > 0, q, 1.0)), 0.0)
        rep_fac = np.zeros((m, T)); np.add.at(rep_fac, owner, rep_src)
        fp_only = fp & ~det_fac
        rep_fac[fp_only] = _false_positive_rates(rng_r, sensor, int(fp_only.sum()))
        det_fac |= fp
        det_fac &= usable[lo:lo + m]
        detected[lo:lo + m] = det_fac
        reported[lo:lo + m] = np.where(det_fac, rep_fac, np.nan).astype(np.float32)
    return CMSLog(sensor.key, fac.astype(np.int64), usable, detected, reported, truth)


# --------------------------------------------------------------------------- driver

def simulate_observations(pop: Population, library: SensorLibrary, plan: DeploymentPlan, seeds: SeedTree, year: int,
                          cache_dir: Path | None = None, force_usable: bool = False, no_false_positives: bool = False) -> ObservationSet:
    """Run every deployed sensor over the population for one year (TDD section 5).

    ``force_usable`` and ``no_false_positives`` are oracle switches for the variance budget (TDD section 6.8).
    """
    keys = tuple(plan.sensor_keys())
    parts: list[ObservationLog] = []
    cms: dict[str, CMSLog] = {}
    counts: dict[str, int] = {}
    for si, key in enumerate(keys):
        sensor = library[key]
        dep = plan.deployments[key]
        if sensor.schedule == "orbit":
            assert sensor.orbit is not None
            kwargs = {} if cache_dir is None else {"cache_dir": cache_dir}
            ov: Overpasses = overpasses_for(key, year, pop.lat, pop.lon, sensor.orbit.swath_km, **kwargs)
            counts[key] = len(ov)
            covered = np.isin(ov.facility_idx, dep.facilities)
            if sensor.constraints.day_only:
                # Night-side passes of a sun-synchronous orbit are never opportunities for a passive
                # sensor; dropping them here halves the log without losing any loggable non-detection.
                covered &= ov.solar_zenith_deg < 90.0
            fac, hour, sza = ov.facility_idx[covered], ov.hour_idx[covered], ov.solar_zenith_deg[covered]
            if sensor.orbit.tasked and dep.taskings_per_year is not None:
                fac, hour, sza = _select_taskings(seeds.rng("observe", "tasking", sensor=key), fac, hour, sza, dep.taskings_per_year)
            parts.append(simulate_snapshot(sensor, si, pop, fac, hour, sza, seeds, force_usable, no_false_positives))
        elif sensor.schedule == "campaign":
            fac, hour = dep.visit_facility, dep.visit_hours
            sza = solar_zenith_deg(hour_index_to_unix(year, hour), pop.lat[fac], pop.lon[fac]).astype(np.float32)
            counts[key] = int(fac.shape[0])
            parts.append(simulate_snapshot(sensor, si, pop, fac, hour, sza, seeds, force_usable, no_false_positives))
        elif sensor.schedule == "survey":
            counts[key] = int(dep.visit_facility.shape[0])
            parts.append(simulate_survey(sensor, si, pop, dep.visit_facility, dep.visit_hours, seeds, year, force_usable, no_false_positives))
        elif sensor.schedule == "hourly":
            counts[key] = int(dep.facilities.shape[0]) * pop.n_hours
            cms[key] = simulate_cms(sensor, pop, dep.facilities, seeds, force_usable=force_usable, no_fp=no_false_positives)
        else:
            raise ValueError(f"unknown schedule {sensor.schedule!r} for {key}")
    log = ObservationLog.concat(parts, keys)
    return ObservationSet(year=year, log=log, cms=cms, overpass_counts=counts,
                          meta={"sensors": list(keys), "n_snapshot_rows": len(log), "n_cms_facilities": {k: int(c.facilities.size) for k, c in cms.items()}})


def _select_taskings(rng: np.random.Generator, fac: np.ndarray, hour: np.ndarray, sza: np.ndarray, per_year: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For tasked satellites keep at most ``per_year`` overpasses per facility, spread over the year.

    Overpasses are binned into ``per_year`` equal slices of the year and one is drawn per
    non-empty slice, so monthly tasking gives roughly monthly looks.
    """
    if fac.size == 0 or per_year <= 0:
        return fac[:0], hour[:0], sza[:0]
    slice_idx = np.minimum((hour * per_year) // HOURS_PER_YEAR, per_year - 1)
    key = fac.astype(np.int64) * per_year + slice_idx
    order = np.lexsort((rng.random(fac.size), key))
    first = np.unique(key[order], return_index=True)[1]
    sel = np.sort(order[first])
    return fac[sel], hour[sel], sza[sel]
