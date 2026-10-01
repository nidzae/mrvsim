"""V6: transient-event low bias with LEO satellites only (TDD section 9) [vlmr-2026].

A single facility releases ``rate_t_h`` for ``duration_h`` hours (a blowdown). The operational satellite-only
estimate of the released mass is the mean of the detected snapshot rates multiplied by the event duration
(what an analyst does with a plume image and a reported duration), or zero when no pass caught the event.
Averaged over many random event start times, the ratio estimate/truth must be below one and of the published
order of magnitude. A time-resolved model (the MRVSim estimator with duration priors) is deliberately not used here:
the test is about what satellites alone can see.
"""

from __future__ import annotations

import copy
import time
from typing import Any

import numpy as np

from mrvsim.io.seeds import SeedTree
from mrvsim.observe.simulator import overpass_band_km
from mrvsim.observe.overpass import overpasses_for
from mrvsim.observe.simulator import simulate_snapshot
from mrvsim.population import generate_population
from mrvsim.population.temporal import StatePaths
from mrvsim.sensors import load_library
from mrvsim.validate.common import default_priors, finish, guard_placeholder
from mrvsim.validate.results import ValidationResult
from mrvsim.validate.targets import load_targets, target_missing

VID, NAME = "V6", "Transient-event underestimation by LEO satellites"
SATS = ("tropomi", "sentinel2_msi", "landsat_oli", "ghgsat_c", "prisma", "enmap")


def run(targets: dict[str, Any] | None = None, n_events: int = 200, seed: int = 20260930) -> ValidationResult:
    t0 = time.perf_counter()
    tg = (targets or load_targets())["V6"]
    priors = default_priors()
    ref = guard_placeholder(VID, NAME, tg["pass_rule"], tg["citations"], priors)
    if ref:
        return ref
    ev = tg["event"]; rate = float(ev["rate_t_h"]) * 1000.0; dur = int(ev["duration_h"])
    lib = load_library()
    base = generate_population({"n_per_stratum": 30}, SeedTree(seed), priors=priors)
    rng = np.random.default_rng(seed)
    # n_events facilities (reuse coordinates), each with one source on for `dur` hours starting at a random daytime-ish hour
    pop = copy.copy(base); idx = np.arange(n_events) % base.n_facilities
    for name in ("stratum_idx", "basin_idx", "ftype_idx", "tclass_idx", "lat", "lon"):
        setattr(pop, name, getattr(base, name)[idx])
    pop.n_sources = np.ones(n_events, np.int64); pop.source_offset = np.arange(n_events + 1); pop.src_facility = np.arange(n_events)
    pop.z = np.ones(n_events, np.int8); pop.q_kg_h = np.full(n_events, rate); pop.pi = np.full(n_events, dur / 8760)
    states = np.zeros((n_events, 8760), dtype=bool); start = rng.integers(0, 8760 - dur, size=n_events)
    for i, s0 in enumerate(start):
        states[i, s0:s0 + dur] = True
    pop.states = StatePaths.from_bool(states)
    cond = copy.copy(base.conditions)
    for name in ("p_cloud", "surface_reflectance", "surface_heterogeneity", "wind_k", "wind_lambda"):
        setattr(cond, name, getattr(base.conditions, name)[idx])
    pop.conditions = cond
    truth_kg = rate * dur
    est = np.zeros(n_events); n_det = np.zeros(n_events, int); n_pass_in_event = np.zeros(n_events, int)
    for si, key in enumerate(SATS):
        s = lib[key]
        ov = overpasses_for(key, 2024, pop.lat, pop.lon, overpass_band_km(s))
        keep = ov.solar_zenith_deg < 90
        log = simulate_snapshot(s, si, pop, ov.facility_idx[keep], ov.hour_idx[keep], ov.solar_zenith_deg[keep], SeedTree(seed + si))
        true_det = log.detected & ~log.oracle_false_positive
        in_event = (log.hour_idx >= start[log.facility_idx]) & (log.hour_idx < start[log.facility_idx] + dur)
        np.add.at(n_pass_in_event, log.facility_idx[in_event & log.usable], 1)
        np.add.at(est, log.facility_idx[true_det], log.reported_kg_h[true_det]); np.add.at(n_det, log.facility_idx[true_det], 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        est_mass = np.where(n_det > 0, est / np.maximum(n_det, 1) * dur, 0.0)
    ratio = est_mass / truth_kg
    mean_ratio = float(ratio.mean()); detected_share = float((n_det > 0).mean())
    pub = tg["published_ratio"]; lo, hi = pub.get("range", [0.0, 1.0])
    ok_direction = mean_ratio < 1.0
    ok_magnitude = (lo <= mean_ratio <= hi) if not target_missing(pub) or pub.get("range") else True
    compared = {"event": ev, "n_events": n_events, "share_of_events_with_any_detection": detected_share,
                "mean_passes_during_event": float(n_pass_in_event.mean()), "mean_ratio_estimate_over_truth": mean_ratio,
                "median_ratio_when_detected": float(np.median(ratio[n_det > 0])) if (n_det > 0).any() else None,
                "published_range": pub.get("range"), "pass_direction": bool(ok_direction), "pass_magnitude": bool(ok_magnitude)}
    status = "pass" if (ok_direction and ok_magnitude) else "fail"
    return finish(ValidationResult(VID, NAME, status, tg["pass_rule"], compared, "", tg["citations"], str(pub.get("status", "verify"))), t0, priors)
