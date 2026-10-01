"""V5: prior misspecification (TDD sections 6.2, 9). Truth from perturbed priors; estimator keeps the base prior."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from mrvsim.estimate import build_inputs, run_fast_estimator
from mrvsim.io.seeds import SeedTree
from mrvsim.observe import build_plan, simulate_observations
from mrvsim.pipeline import sensor_modes
from mrvsim.population import generate_population
from mrvsim.population.priors import perturb_priors
from mrvsim.sensors import load_library
from mrvsim.validate.common import default_priors, finish, guard_placeholder
from mrvsim.validate.results import ValidationResult
from mrvsim.validate.targets import load_targets

VID, NAME = "V5", "Prior misspecification robustness"


def coverage_under(perturb: dict[str, float], n_per_stratum: int, n_draws: int, step: int, seed: int) -> dict[str, float]:
    lib = load_library(); base = default_priors()
    truth_priors = perturb_priors(base, **perturb)
    seeds = SeedTree(seed)
    pop = generate_population({"n_per_stratum": n_per_stratum}, seeds.child(rep=0), priors=truth_priors)
    policy = {"sensors": {"bridger_gml": {"coverage": 1.0, "frequency_per_year": 2}, "cms_generic": {"coverage": 0.2, "targeting": "throughput"},
                          "ghgsat_c": {"coverage": 0.3, "frequency_per_year": 12, "targeting": "throughput"}}}
    plan = build_plan(policy, seeds, pop.n_facilities, pop.lon, pop.throughput.gas_mkt_m3_yr, sensor_modes(lib), 2024)
    obs = simulate_observations(pop, lib, plan, seeds.child(rep=0), 2024)
    inputs = build_inputs(pop, obs, lib, seeds)
    sub = np.arange(0, pop.n_facilities, step)
    post = run_fast_estimator(inputs, pop.strata, base, lib, seeds, n_draws=n_draws, facilities=sub)   # base (unperturbed) prior
    return {"kappa_mass": float(post.covers(pop.true_mass_kg_yr())[sub].mean()), "kappa_intensity": float(post.covers(pop.true_intensity(), "intensity")[sub].mean()),
            "bias_mass": float(np.median(np.log(post.mass_kg_yr_p50[sub] / pop.true_mass_kg_yr()[sub]))), "n": int(sub.size)}


def run(targets: dict[str, Any] | None = None, n_per_stratum: int = 30, n_draws: int = 2000, step: int = 6, seed: int = 20260930) -> ValidationResult:
    t0 = time.perf_counter()
    tg = (targets or load_targets())["V5"]
    priors = default_priors()
    ref = guard_placeholder(VID, NAME, tg["pass_rule"], tg["citations"], priors)
    if ref:
        return ref
    d = tg["perturbations"]; kmin = float(tg["kappa_min"])
    cases = {"alpha+": {"alpha": +d["alpha"]}, "alpha-": {"alpha": -d["alpha"]}, "mu+": {"mu": +d["mu"]}, "mu-": {"mu": -d["mu"]},
             "nu_on+": {"nu_on": +d["nu_on"]}, "nu_on-": {"nu_on": -d["nu_on"]}}
    compared: dict[str, Any] = {}; verdicts = []
    for label, pert in cases.items():
        r = coverage_under(pert, n_per_stratum, n_draws, step, seed); ok = r["kappa_mass"] >= kmin and r["kappa_intensity"] >= kmin
        compared[label] = {**r, "perturbation": pert, "pass": bool(ok)}; verdicts.append(bool(ok))
    joint = coverage_under({"alpha": d["alpha"], "mu": d["mu"], "nu_on": d["nu_on"]}, n_per_stratum, n_draws, step, seed)
    compared["joint"] = {**joint, "perturbation": {"alpha": d["alpha"], "mu": d["mu"], "nu_on": d["nu_on"]}, "pass": None, "note": "reported, not scored"}
    return finish(ValidationResult(VID, NAME, "pass" if all(verdicts) else "fail", tg["pass_rule"], compared, "", tg["citations"], "ok"), t0, priors)
