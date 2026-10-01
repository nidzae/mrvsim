"""Observation simulator tests (TDD section 5; CLAUDE.md Phase 3)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pytest

from mrvsim.io.seeds import SeedTree
from mrvsim.observe import build_plan, overpasses_for, simulate_observations, solar_zenith_deg
from mrvsim.observe.overpass import TLE_DIR
from mrvsim.observe.simulator import month_of_hour, simulate_cms, simulate_snapshot
from mrvsim.population import generate_population
from mrvsim.population.temporal import StatePaths
from mrvsim.sensors import load_library

LIB = load_library()
YEAR = 2024
CACHE = Path(__file__).resolve().parents[1] / "_cache_overpasses"


def _modes():
    return {s.key: (s.schedule, s.observation_mode, bool(s.orbit.tasked) if s.orbit else False) for s in LIB}


@pytest.fixture(scope="module")
def pop():
    return generate_population({"n_per_stratum": 30}, SeedTree(3))


def _single_source_population(pop, rate_kg_h: float, n_fac: int = 40):
    """Copy of the first n facilities with exactly one steady source each at ``rate_kg_h``."""
    import copy

    p = copy.copy(pop)
    idx = np.arange(n_fac)
    p.stratum_idx, p.basin_idx, p.ftype_idx, p.tclass_idx = (a[idx] for a in (pop.stratum_idx, pop.basin_idx, pop.ftype_idx, pop.tclass_idx))
    p.lat, p.lon = pop.lat[idx], pop.lon[idx]
    p.n_sources = np.ones(n_fac, np.int64); p.source_offset = np.arange(n_fac + 1)
    p.src_facility = np.arange(n_fac); p.z = np.zeros(n_fac, np.int8); p.q_kg_h = np.full(n_fac, rate_kg_h); p.pi = np.ones(n_fac)
    p.nu_on = p.tau_on = p.nu_off = p.tau_off = np.zeros(n_fac)
    p.states = StatePaths.from_bool(np.ones((n_fac, pop.n_hours), dtype=bool))
    import copy as _c
    cond = _c.copy(pop.conditions)
    cond.p_cloud = pop.conditions.p_cloud[idx]; cond.surface_reflectance = pop.conditions.surface_reflectance[idx]
    cond.surface_heterogeneity = pop.conditions.surface_heterogeneity[idx]; cond.wind_k = pop.conditions.wind_k[idx]; cond.wind_lambda = pop.conditions.wind_lambda[idx]
    p.conditions = cond
    thr = _c.copy(pop.throughput)
    for name in ("gas_mkt_m3_yr", "oil_bbl_yr", "x_ch4", "g_ch4_kg_yr", "f_gas", "mmbtu_yr", "ghgrp_reporter"):
        setattr(thr, name, getattr(pop.throughput, name)[idx])
    p.throughput = thr
    return p


def test_solar_zenith_sanity() -> None:
    # Local solar noon at lon 0 on the June solstice, lat 23.44 -> sun overhead
    t = dt.datetime(2024, 6, 20, 12, 0, tzinfo=dt.timezone.utc).timestamp()
    assert solar_zenith_deg(t, 23.44, 0.0) < 1.5
    # midnight -> sun well below horizon
    t2 = dt.datetime(2024, 6, 20, 0, 0, tzinfo=dt.timezone.utc).timestamp()
    assert solar_zenith_deg(t2, 40.0, 0.0) > 100
    # Permian, 18:30 UTC ~ 12:30 local in January: zenith ~ 55 degrees
    t3 = dt.datetime(2024, 1, 15, 18, 30, tzinfo=dt.timezone.utc).timestamp()
    assert 50 < solar_zenith_deg(t3, 32.0, -103.0) < 60


def test_month_of_hour() -> None:
    assert month_of_hour(np.array([0, 743, 744, 8759])).tolist() == [0, 0, 1, 11]


@pytest.mark.parametrize("key,lo,hi", [("tropomi", 500, 950), ("sentinel2_msi", 60, 130), ("landsat_oli", 35, 85)])
def test_overpass_counts_realistic(pop, key: str, lo: int, hi: int) -> None:
    """Passes per facility per year, day and night sides both counted (the sun gate is applied later).

    Geometry at ~32 N: TROPOMI 14 orbits/day x 2600 km swath -> ~2 passes/day (~730/yr, more with overlap);
    Sentinel-2A 10-day repeat, 290 km swath vs 238 km orbit spacing -> ~44 day + 44 night (~88/yr);
    Landsat 9 16-day repeat, 185 km swath vs 146 km spacing -> ~29 + 29 (~58/yr).
    """
    s = LIB[key]
    n = 40
    ov = overpasses_for(key, YEAR, pop.lat[:n], pop.lon[:n], s.orbit.swath_km, cache_dir=CACHE)
    per_fac = np.bincount(ov.facility_idx, minlength=n)
    assert per_fac.min() > 0
    med = float(np.median(per_fac))
    assert lo <= med <= hi, (key, med)
    assert np.all(ov.cross_track_km <= s.orbit.swath_km / 2 + 1e-6)
    assert np.all((ov.hour_idx >= 0) & (ov.hour_idx < 8760))
    # passes for one facility are separated by more than 10 minutes
    for f in range(3):
        t = np.sort(ov.unix_time_s[ov.facility_idx == f])
        assert np.all(np.diff(t) > 600)


def test_overpass_local_time_is_daytime_for_sun_synchronous(pop) -> None:
    """Sentinel-5P crosses ~13:30 local: SZA at pass should be daytime for roughly half the passes (descending node at night)."""
    s = LIB["tropomi"]
    ov = overpasses_for("tropomi", YEAR, pop.lat[:20], pop.lon[:20], s.orbit.swath_km, cache_dir=CACHE)
    day = (ov.solar_zenith_deg < 90).mean()
    assert 0.35 < day < 0.65


def test_overpass_cache_round_trip(pop, tmp_path) -> None:
    s = LIB["landsat_oli"]
    a = overpasses_for("landsat_oli", YEAR, pop.lat[:5], pop.lon[:5], s.orbit.swath_km, cache_dir=tmp_path)
    b = overpasses_for("landsat_oli", YEAR, pop.lat[:5], pop.lon[:5], s.orbit.swath_km, cache_dir=tmp_path)
    np.testing.assert_array_equal(a.unix_time_s, b.unix_time_s)
    assert len(list(tmp_path.glob("*.npz"))) == 1


def test_tropomi_detects_10_t_h_on_most_clear_overpasses(pop) -> None:
    """CLAUDE.md Phase 3 acceptance test, relaxed per DECISION_LOG 2026-10-01: Schuit 2023 puts the TROPOMI
    detection limit at ~5 t/h under favourable conditions (detected-plume 5th percentile 8 t/h), so a 10 t/h
    source is detected on most, not nearly all, clear passes."""
    p = _single_source_population(pop, 10_000.0)
    s = LIB["tropomi"]
    ov = overpasses_for("tropomi", YEAR, p.lat, p.lon, s.orbit.swath_km, cache_dir=CACHE)
    log = simulate_snapshot(s, 0, p, ov.facility_idx, ov.hour_idx, ov.solar_zenith_deg, SeedTree(11))
    clear = log.usable
    assert clear.sum() > 500
    frac = log.detected[clear].mean()
    assert frac > 0.5, frac
    # non-detections and cloud-outs are logged with conditions
    assert (~log.usable).sum() > 0 and np.all(np.isfinite(log.wind_m_s))
    assert np.isnan(log.reported_kg_h[~log.detected]).all()
    assert np.all(log.reported_kg_h[log.detected & ~log.oracle_false_positive] > 0)


@pytest.mark.parametrize("key", ["tropomi", "sentinel2_msi", "landsat_oli", "ghgsat_c", "prisma", "enmap", "tanager1"])
def test_5_kg_h_never_detected_by_any_satellite(pop, key: str) -> None:
    """CLAUDE.md Phase 3 acceptance test (true detections; false positives are the separate lambda_FP process)."""
    p = _single_source_population(pop, 5.0)
    s = LIB[key]
    ov = overpasses_for(key, YEAR, p.lat, p.lon, s.orbit.swath_km, cache_dir=CACHE)
    log = simulate_snapshot(s, 0, p, ov.facility_idx, ov.hour_idx, ov.solar_zenith_deg, SeedTree(12))
    true_det = log.detected & ~log.oracle_false_positive
    assert true_det.sum() == 0
    assert s.pod.prob(5.0, wind_m_s=3.0, rho_surf=0.3) < 1e-2


def test_cms_log_has_8760_rows_per_facility(pop) -> None:
    """CLAUDE.md Phase 3 acceptance test."""
    s = LIB["cms_generic"]
    # facilities whose sources are all intermittent, so the series contains zero-rate hours
    all_inter = np.array([pop.z[pop.source_offset[i]:pop.source_offset[i + 1]].all() for i in range(pop.n_facilities)])
    fac = np.nonzero(all_inter)[0][:7]
    assert fac.size == 7
    cms = simulate_cms(s, pop, fac, SeedTree(13))
    assert cms.detected.shape == (7, 8760)
    assert cms.rows_for_facility(int(fac[3])) == 8760 and cms.rows_for_facility(999_999) == 0
    assert cms.n_rows == 7 * 8760
    assert abs(cms.usable.mean() - (1 - 0.03) * 0.85) < 0.01   # outage_probability x wind_sector_coverage from the YAML
    # detections follow truth: hours with a large true rate are mostly detected, zero-rate hours rarely
    big = cms.oracle_true_rate_kg_h > 20
    if big.any():
        assert cms.detected[big & cms.usable].mean() > 0.9
    zero = (cms.oracle_true_rate_kg_h == 0) & cms.usable
    assert cms.detected[zero].mean() < 0.01


def test_full_simulation_and_reproducibility(pop, tmp_path) -> None:
    policy = {"sensors": {
        "bridger_gml": {"coverage": 0.5, "frequency_per_year": 2, "targeting": "random"},
        "ghgsat_c": {"coverage": 0.2, "frequency_per_year": 12, "targeting": "throughput"},
        "tropomi": {"coverage": 1.0},
        "cms_generic": {"coverage": 0.1, "targeting": "throughput"},
        "ogi": {"coverage": 0.2, "frequency_per_year": 1},
    }}
    small = _single_source_population(pop, 50.0, n_fac=60)
    small.n_sources = pop.n_sources[:60]; small.source_offset = pop.source_offset[:61]
    small.src_facility = pop.src_facility[: small.source_offset[-1]]; small.z = pop.z[: small.source_offset[-1]]
    small.q_kg_h = pop.q_kg_h[: small.source_offset[-1]]; small.pi = pop.pi[: small.source_offset[-1]]
    small.states = StatePaths(pop.states.packed[: small.source_offset[-1]], pop.n_hours)

    def run(seed: int):
        seeds = SeedTree(seed)
        plan = build_plan(policy, seeds, small.n_facilities, small.lon, small.throughput.gas_mkt_m3_yr, _modes(), YEAR)
        return simulate_observations(small, LIB, plan, seeds, YEAR, cache_dir=CACHE)

    a, b, c = run(5), run(5), run(6)
    assert len(a.log) > 0 and set(a.log.sensor_keys) == set(policy["sensors"])
    for name in ("facility_idx", "hour_idx", "detected"):
        np.testing.assert_array_equal(getattr(a.log, name), getattr(b.log, name))
    np.testing.assert_array_equal(a.log.reported_kg_h, b.log.reported_kg_h)
    np.testing.assert_array_equal(a.cms["cms_generic"].detected, b.cms["cms_generic"].detected)
    assert not np.array_equal(a.log.detected, c.log.detected) or len(a.log) == 0
    # tasked satellite: at most 12 looks per facility
    gh = (a.log.sensor_idx == list(a.log.sensor_keys).index("ghgsat_c")) & ~a.log.incidental
    assert np.bincount(a.log.facility_idx[gh]).max() <= 12
    # aircraft: exactly 2 visits per covered facility
    br = a.log.sensor_idx == list(a.log.sensor_keys).index("bridger_gml")
    assert set(np.bincount(a.log.facility_idx[br])[np.bincount(a.log.facility_idx[br]) > 0].tolist()) == {2}
    # OGI rows are per source
    og = a.log.sensor_idx == list(a.log.sensor_keys).index("ogi")
    assert np.all(a.log.source_idx[og] >= 0) and np.all(a.log.source_idx[~og & ~gh & ~br] == -1)
    # save / load round trip
    a.save(tmp_path)
    from mrvsim.observe.simulator import CMSLog, ObservationLog
    back = ObservationLog.load(tmp_path / "observations.npz")
    np.testing.assert_array_equal(back.detected, a.log.detected)
    cb = CMSLog.load(tmp_path / "cms_cms_generic.npz")
    np.testing.assert_array_equal(cb.detected, a.cms["cms_generic"].detected)
