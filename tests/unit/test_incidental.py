"""Field of regard, scene footprint and incidental capture (TDD section 5.1 as amended; DECISION_LOG 2026-10-01 "Incidental capture")."""

from __future__ import annotations

import numpy as np
import pytest

from mrvsim.io.seeds import SeedTree
from mrvsim.observe.deployment import DeploymentPlan, SensorDeployment
from mrvsim.observe.overpass import ground_reach_km, tasking_band_km, tle_altitude_km, TLE_DIR
from mrvsim.observe.simulator import ObservationLog, overpass_band_km, scene_neighbours, simulate_observations
from mrvsim.population import generate_population
from mrvsim.score.cost import deployment_cost
from mrvsim.sensors import load_library
from mrvsim.policy import Policy

LIB = load_library()
YEAR = 2024


def test_pointing_reach_geometry() -> None:
    # flat-Earth limit for small angles: h tan(theta); spherical correction grows with angle
    assert abs(ground_reach_km(500.0, 15.0) - 500.0 * np.tan(np.deg2rad(15.0))) / (500.0 * np.tan(np.deg2rad(15.0))) < 0.05
    assert ground_reach_km(500.0, 0.0) == 0.0
    assert ground_reach_km(650.0, 30.0) > ground_reach_km(650.0, 15.0) > 0
    alt = tle_altitude_km(TLE_DIR / "ghgsat_c.tle")
    assert 400.0 < alt < 560.0, alt                              # GHGSat-C2 flies a ~500 km SSO [ghgsat-c1-spec]
    band = overpass_band_km(LIB["ghgsat_c"])
    assert band == tasking_band_km(12.0, 15.0, alt) and 200.0 < band < 320.0, band
    assert overpass_band_km(LIB["tropomi"]) == LIB["tropomi"].orbit.swath_km   # wall-to-wall: unchanged


def _pop(seed: int = 5):
    """Smallest allowed population; facility 0 is moved to an empty patch of ocean so only the facilities the
    test places next to it can fall inside a scene (the TLE-propagated orbit covers it like anywhere else)."""
    pop = generate_population({"n_per_stratum": 2, "min_per_stratum": 2}, SeedTree(seed))
    pop.lat[0], pop.lon[0] = 35.0, -140.0
    return pop


def test_scene_neighbours_rectangle_and_heading() -> None:
    pop = _pop()
    # put three facilities in a line east of facility 0 at 3, 5 and 9 km, and one 3 km north
    lat0, lon0 = float(pop.lat[0]), float(pop.lon[0])
    kx = 111.32 * np.cos(np.deg2rad(lat0))
    pop.lat[1], pop.lon[1] = lat0, lon0 + 3.0 / kx
    pop.lat[2], pop.lon[2] = lat0, lon0 + 5.0 / kx
    pop.lat[3], pop.lon[3] = lat0, lon0 + 9.0 / kx
    pop.lat[4], pop.lon[4] = lat0 + 3.0 / 110.574, lon0
    targets = np.array([0])
    # 12 x 12 km square, heading north: 3 and 5 km east inside (half-width 6), 9 km outside, 3 km north inside
    rows, nb = scene_neighbours(pop, targets, np.array([0.0]), 12.0, 12.0)
    assert set(nb.tolist()) == {1, 2, 4}
    # a 12 km (along) x 4 km (cross) strip heading north: only the northern neighbour is inside
    rows, nb = scene_neighbours(pop, targets, np.array([0.0]), 12.0, 4.0)
    assert set(nb.tolist()) == {4}
    # same strip heading east: the eastern neighbours at 3 and 5 km are inside, the northern one is outside the 4 km cross width
    rows, nb = scene_neighbours(pop, targets, np.array([90.0]), 12.0, 4.0)
    assert set(nb.tolist()) == {1, 2}
    # axis-aligned 0.5 km block: nothing
    rows, nb = scene_neighbours(pop, targets, None, 0.5, 0.5)
    assert nb.size == 0


def _two_site_plan(pop, key: str, target: int, hours: np.ndarray | None = None) -> DeploymentPlan:
    plan = DeploymentPlan(year=YEAR)
    if key == "bridger_gml":
        plan.deployments[key] = SensorDeployment(key, np.array([target]), hours, np.full(hours.size, target))
    else:
        plan.deployments[key] = SensorDeployment(key, np.array([target]), taskings_per_year=12)
    return plan


def test_tasked_scene_captures_neighbour_not_far_site() -> None:
    pop = _pop(6)
    lat0, lon0 = float(pop.lat[0]), float(pop.lon[0]); kx = 111.32 * np.cos(np.deg2rad(lat0))
    pop.lat[1], pop.lon[1] = lat0 + 2.0 / 110.574, lon0 + 2.0 / kx      # ~2.8 km away: inside any 12 km scene
    pop.lat[2], pop.lon[2] = lat0, lon0 + 25.0 / kx                      # 25 km: outside
    plan = _two_site_plan(pop, "ghgsat_c", 0)
    seeds = SeedTree(11)
    obs = simulate_observations(pop, LIB, plan, seeds, YEAR, cache_dir=None)
    log = obs.log
    tasked = (log.facility_idx == 0) & ~log.incidental
    inc1 = (log.facility_idx == 1) & log.incidental
    assert tasked.sum() > 0 and inc1.sum() > 0 and not ((log.facility_idx == 2)).any()
    assert set(log.hour_idx[inc1].tolist()) <= set(log.hour_idx[tasked].tolist())      # same passes
    assert np.all(log.scene_target_idx[inc1] == 0)
    # same scene, same sky: cloud state copied from the target's row of that hour
    by_hour = dict(zip(log.hour_idx[tasked].tolist(), log.cloud_blocked[tasked].tolist()))
    assert all(bool(by_hour[int(h)]) == bool(c) for h, c in zip(log.hour_idx[inc1], log.cloud_blocked[inc1]))
    # incidental rows cost nothing
    cost = deployment_cost(plan, obs, LIB, pop.n_facilities)
    assert cost.by_sensor_usd["ghgsat_c"] == pytest.approx(LIB["ghgsat_c"].cost.per_tasking_usd * int(tasked.sum()))
    # switch off: nothing incidental, target rows identical
    obs_off = simulate_observations(pop, LIB, plan, seeds, YEAR, cache_dir=None, incidental_capture=False)
    assert not obs_off.log.incidental.any() and len(obs_off.log) == int(tasked.sum())
    np.testing.assert_array_equal(obs_off.log.hour_idx, log.hour_idx[tasked])
    assert obs.meta["incidental_capture"] is True and obs.meta["n_incidental_rows"] == int(log.incidental.sum())


def test_both_tasked_same_pass_logged_once() -> None:
    pop = _pop(7)
    lat0, lon0 = float(pop.lat[0]), float(pop.lon[0]); kx = 111.32 * np.cos(np.deg2rad(lat0))
    pop.lat[1], pop.lon[1] = lat0, lon0 + 1.0 / kx
    plan = DeploymentPlan(year=YEAR); plan.deployments["ghgsat_c"] = SensorDeployment("ghgsat_c", np.array([0, 1]), taskings_per_year=12)
    obs = simulate_observations(pop, LIB, plan, SeedTree(12), YEAR, cache_dir=None)
    log = obs.log
    pairs = list(zip(log.facility_idx.tolist(), log.hour_idx.tolist()))
    assert len(pairs) == len(set(pairs))                                  # no (facility, hour) duplicates
    # a neighbour tasked on a different pass is still captured incidentally on this one
    assert log.incidental.any()


def test_aircraft_survey_block_captures_close_neighbour() -> None:
    pop = _pop(8)
    lat0, lon0 = float(pop.lat[0]), float(pop.lon[0]); kx = 111.32 * np.cos(np.deg2rad(lat0))
    pop.lat[1], pop.lon[1] = lat0, lon0 + 0.15 / kx     # 150 m: inside the 0.5 km block
    pop.lat[2], pop.lon[2] = lat0, lon0 + 2.0 / kx      # 2 km: outside
    hours = np.array([1000, 5000])
    plan = _two_site_plan(pop, "bridger_gml", 0, hours)
    obs = simulate_observations(pop, LIB, plan, SeedTree(13), YEAR)
    log = obs.log
    assert set(log.facility_idx.tolist()) == {0, 1}
    assert set(log.hour_idx[log.facility_idx == 1].tolist()) == set(hours.tolist()) and log.incidental[log.facility_idx == 1].all()
    assert deployment_cost(plan, obs, LIB, pop.n_facilities).by_sensor_usd["bridger_gml"] == pytest.approx(2 * LIB["bridger_gml"].cost.per_site_visit_usd)


def test_log_round_trip_and_legacy_load(tmp_path) -> None:
    log = ObservationLog(facility_idx=np.array([0, 1]), source_idx=np.array([-1, -1]), sensor_idx=np.array([0, 0], np.int16), hour_idx=np.array([5, 5]),
                         usable=np.array([True, True]), cloud_blocked=np.zeros(2, bool), sun_blocked=np.zeros(2, bool), wind_blocked=np.zeros(2, bool),
                         detected=np.array([True, False]), reported_kg_h=np.array([3.0, np.nan]), wind_m_s=np.ones(2, np.float32), rho_surf=np.ones(2, np.float32),
                         solar_zenith_deg=np.ones(2, np.float32), oracle_true_rate_kg_h=np.array([3.0, 0.0]), oracle_false_positive=np.zeros(2, bool),
                         incidental=np.array([False, True]), scene_target_idx=np.array([-1, 0]), sensor_keys=("ghgsat_c",))
    log.save(tmp_path / "log.npz")
    back = ObservationLog.load(tmp_path / "log.npz")
    np.testing.assert_array_equal(back.incidental, log.incidental); np.testing.assert_array_equal(back.scene_target_idx, log.scene_target_idx)
    # a log written before the columns existed loads with defaults
    z = dict(np.load(tmp_path / "log.npz")); z.pop("incidental"); z.pop("scene_target_idx")
    np.savez_compressed(tmp_path / "old.npz", **z)
    old = ObservationLog.load(tmp_path / "old.npz")
    assert not old.incidental.any() and np.all(old.scene_target_idx == -1) and len(old) == 2


def test_policy_flag_round_trip() -> None:
    pol = Policy.from_policy_cfg({"sensors": {"ghgsat_c": {"coverage": 0.3}}, "incidental_capture": False})
    assert pol.incidental_capture is False and pol.to_policy_cfg()["incidental_capture"] is False
    assert Policy.from_yaml(pol.to_yaml()).incidental_capture is False
    assert Policy.from_policy_cfg({"sensors": {}}).incidental_capture is True
