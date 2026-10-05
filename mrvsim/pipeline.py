"""End-to-end pipeline: config -> population -> observation -> estimator -> scoring (TDD section 1).

``run_replication`` executes one Monte Carlo replication; ``run_scored`` loops
over R replications, aggregates with Monte Carlo standard errors, and persists
everything under ``runs/<id>/`` through :class:`~mrvsim.io.run.RunContext`.

Interactive runs (PRD N3) use the ``estimator.interactive`` block: fewer draws,
a per-stratum facility subsample, and fewer replications; results are scaled
by stratum weights exactly as full runs are.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from mrvsim.estimate import build_inputs, run_fast_estimator
from mrvsim.estimate.fast import PosteriorSummary
from mrvsim.io.config import RunConfig
from mrvsim.io.run import RunContext
from mrvsim.io.seeds import SeedTree
from mrvsim.observe import DeploymentPlan, ObservationSet, build_plan, simulate_observations
from mrvsim.population import Population, generate_population
from mrvsim.score.observed import ObservedBounds, observed_bounds
from mrvsim.score import (
    Bar, ReplicationScores, ScoreReport, aggregate, completeness_from_detection_probability, cost_metrics, deployment_cost,
    detected_mass_kg, detection_probability_once, score_replication,
)
from mrvsim.sensors import SensorLibrary, load_library


@dataclass
class ReplicationResult:
    rep: int
    pop: Population
    plan: DeploymentPlan
    obs: ObservationSet
    post: PosteriorSummary
    scores: ReplicationScores
    scored_facilities: np.ndarray
    extras: dict[str, Any] = field(default_factory=dict)
    observed: ObservedBounds | None = None      # observation-only bounds (PRD section 5.3a)


def sensor_modes(library: SensorLibrary) -> dict[str, tuple[str, str, bool]]:
    return {s.key: (s.schedule, s.observation_mode, bool(s.orbit.tasked) if s.orbit else False) for s in library}


def _facility_subsample(pop: Population, per_stratum: int | None, rng: np.random.Generator) -> np.ndarray:
    if per_stratum is None:
        return np.arange(pop.n_facilities)
    out = []
    for h in range(len(pop.strata)):
        idx = np.nonzero(pop.stratum_idx == h)[0]
        out.append(idx if idx.size <= per_stratum else np.sort(rng.choice(idx, per_stratum, replace=False)))
    return np.concatenate(out)


def run_replication(cfg: RunConfig, seeds: SeedTree, rep: int, library: SensorLibrary | None = None,
                    n_draws: int | None = None, facilities_per_stratum: int | None = None, cache_dir: Path | None = None) -> ReplicationResult:
    library = library or load_library()
    rs = seeds.child(rep=rep)
    pop = generate_population(cfg.population, rs, coordinate_seeds=seeds)   # locations fixed across replications
    w_count, w_thr = pop.stratum_weights("count"), pop.stratum_weights("throughput")
    policy = {**cfg.policy}; policy.setdefault("coverage_weighting", "population")     # TDD section 8.1 as amended 2026-10-05
    plan = build_plan(policy, rs, pop.n_facilities, pop.lon, pop.throughput.gas_mkt_m3_yr, sensor_modes(library), cfg.year, basin_idx=pop.basin_idx,
                      count_weights=w_count, throughput_weights=w_thr)
    obs = simulate_observations(pop, library, plan, rs, cfg.year, cache_dir=cache_dir, incidental_capture=bool(cfg.policy.get("incidental_capture", True)))
    inputs = build_inputs(pop, obs, library, rs)
    est = cfg.estimator
    nd = int(n_draws or est.get("n_draws", 10_000))
    sub = _facility_subsample(pop, facilities_per_stratum, rs.rng("pipeline", "subsample"))
    post = run_fast_estimator(inputs, pop.strata, pop.priors, library, rs, n_draws=nd, facilities=sub,
                              method=str(est.get("sampler", "auto")), realised=bool(est.get("realised_estimand", True)))
    # truth and scoring
    truth_m = pop.true_mass_kg_yr(); truth_i = pop.true_intensity()
    bars = {"mass": Bar.from_config(cfg.scoring, "mass"), "intensity": Bar.from_config(cfg.scoring, "intensity")}
    p_once = detection_probability_once(pop, obs, library)
    comp, comp_b = completeness_from_detection_probability(
        pop.q_kg_h, pop.states.on_hours(), p_once, pop.src_facility, pop.basin_idx, list(pop.strata.basins), pop.constants.completeness_threshold_kg_h)
    weighted = policy["coverage_weighting"] == "population"
    cost = deployment_cost(plan, obs, library, pop.n_facilities, count_weights=w_count if weighted else None)
    scored = np.zeros(pop.n_facilities, dtype=bool); scored[sub] = True
    # detected mass: sources whose facility had at least one true detection by any sensor (snapshot) or per-source detection
    det_src = np.zeros(pop.n_total_sources, dtype=bool)
    log = obs.log
    true_det = log.detected & ~log.oracle_false_positive
    fac_det = np.unique(log.facility_idx[true_det & (log.source_idx < 0)])
    for f in fac_det:
        det_src[pop.source_offset[f]:pop.source_offset[f + 1]] |= pop.states.on_hours()[pop.source_offset[f]:pop.source_offset[f + 1]] > 0
    det_src[log.source_idx[true_det & (log.source_idx >= 0)]] = True
    for c in obs.cms.values():
        for fi, f in enumerate(c.facilities):
            if c.detected[fi].any():
                det_src[pop.source_offset[f]:pop.source_offset[f + 1]] = True
    dm = detected_mass_kg(pop.q_kg_h, pop.states.on_hours(), p_once, det_src,
                          source_weights=(pop.n_facilities * w_count)[pop.src_facility] if weighted else None)
    pre = score_replication(post, truth_m, truth_i, pop.stratum_idx, w_count, w_thr,
                            pop.throughput.mmbtu_yr, bars, comp, comp_b, {}, len(pop.strata), scored)
    # Observation-only view (PRD section 5.3a): bounds from measurements alone, for every facility (no estimator needed).
    ob = observed_bounds(obs, library, pop.n_facilities, inputs.g_hat_kg_yr, pop.throughput.f_gas, pop.throughput.gas_mkt_m3_yr,
                         cap=str(cfg.scoring.get("unobserved_cap", "none")))
    well_pad = pop.site_row >= 0 if pop.site_row is not None else np.zeros(pop.n_facilities, dtype=bool)
    for kpi in ("mass", "intensity"):
        st_o = ob.states(kpi, bars[kpi].B)
        m = pre.kpi_metrics[kpi]
        m["observed_certified_share_facilities"] = float(w_count[st_o == 0].sum())
        m["observed_fails_share_facilities"] = float(w_count[st_o == 1].sum())
        m["observed_certified_share_weighted_throughput"] = float(w_thr[st_o == 0].sum())
        m["observed_share_of_year"] = float((w_count * ob.observed_hours).sum() / 8760.0)
        for name, seg in (("well_pads", well_pad), ("midstream", ~well_pad)):
            ws = w_count[seg].sum()
            m[f"observed_certified_share_{name}"] = float(w_count[seg & (st_o == 0)].sum() / ws) if ws > 0 else float("nan")
            m[f"observed_fails_share_{name}"] = float(w_count[seg & (st_o == 1)].sum() / ws) if ws > 0 else float("nan")
            # the estimate's verdicts for the same segment, so the two views can be read side by side
            sc_w = np.where(scored & seg, w_count, 0.0); tot = sc_w.sum(); st_e = pre.states[kpi]
            m[f"certified_share_{name}"] = float(sc_w[st_e == 0].sum() / tot) if tot > 0 else float("nan")
            m[f"fails_share_{name}"] = float(sc_w[st_e == 1].sum() / tot) if tot > 0 else float("nan")
    cm = cost_metrics(cost, dm, pre.kpi_metrics["intensity"]["certified_mmbtu"])
    cm.update({f"cost_{k}_usd": v for k, v in cost.by_sensor_usd.items()})
    pre.cost = cm
    pre.extras = {"n_smc": post.meta.get("n_smc"), "n_low_ess": post.meta.get("n_low_ess"), "detected_mass_kg": dm,
                  "n_snapshot_rows": len(obs.log), "overpass_counts": obs.overpass_counts}
    return ReplicationResult(rep, pop, plan, obs, post, pre, sub, observed=ob)


def run_scored(cfg: RunConfig, root: str | Path = "runs", run_id: str | None = None, library: SensorLibrary | None = None,
               replications: int | None = None, n_draws: int | None = None, facilities_per_stratum: int | None = None,
               keep_last: bool = True, cache_dir: Path | None = None) -> tuple[ScoreReport, RunContext, ReplicationResult | None]:
    """Run R replications, aggregate, persist. Returns (report, run context, last replication for drill-down)."""
    library = library or load_library()
    if "coverage_weighting" not in cfg.policy:      # recorded in the run's config so a reloaded run rebuilds the same plan
        cfg = cfg.with_overrides(policy={**cfg.policy, "coverage_weighting": "population"})
    R = int(replications or cfg.replications)
    reps: list[ReplicationScores] = []
    last: ReplicationResult | None = None
    with RunContext(cfg, root=root, run_id=run_id) as run:
        for r in range(R):
            with run.stage(f"replication_{r}"):
                res = run_replication(cfg, run.seeds, r, library, n_draws, facilities_per_stratum, cache_dir)
            reps.append(res.scores)
            last = res if keep_last else None
        pop0 = last.pop if last is not None else None
        n_strata = len(pop0.strata) if pop0 else 0
        basins = list(pop0.strata.basins) if pop0 else []
        report = aggregate(reps, n_strata, basins)
        report.meta = {"sensors": list(cfg.policy.get("sensors", {}).keys()), "n_draws": n_draws or cfg.estimator.get("n_draws", 10_000), "n_scored": int(reps[-1].n_scored),
                       "facilities_per_stratum": facilities_per_stratum, "replications": R,
                       "priors_provenance": pop0.priors.provenance if pop0 else None, "strata_provenance": pop0.strata.provenance if pop0 else None,
                       "citation_keys": sorted(set(pop0.citation_keys()) | set(library.citation_keys(list(cfg.policy.get("sensors", {}).keys()))) if pop0 else [])}
        run.save_json("summary", _report_json(report))
        if last is not None:
            # rows: p05, p10, p50, p90, p95 (two-sided 90 % interval ends, one-sided 90 % bounds, median)
            run.save_array("posterior_mass_pcts", np.stack([last.post.mass_kg_yr_p05, last.post.mass_kg_yr_p10, last.post.mass_kg_yr_p50, last.post.mass_kg_yr_p90, last.post.mass_kg_yr_p95]))
            run.save_array("posterior_intensity_pcts", np.stack([last.post.intensity_p05, last.post.intensity_p10, last.post.intensity_p50, last.post.intensity_p90, last.post.intensity_p95]))
            run.save_array("true_mass_kg_yr", last.pop.true_mass_kg_yr())
            run.save_array("true_intensity", last.pop.true_intensity())
            run.save_array("state_mass", last.scores.states["mass"])
            run.save_array("state_intensity", last.scores.states["intensity"])
            # quantile grid (QGRID rows), prior summary (p05, p50, p95) and evidence counts (DECISION_LOG 2026-10-01)
            if last.post.mass_quantiles is not None:
                run.save_array("posterior_mass_quantiles", last.post.mass_quantiles)
                run.save_array("posterior_intensity_quantiles", last.post.intensity_quantiles)
            if last.post.prior_mass_pcts is not None:
                run.save_array("prior_mass_pcts", last.post.prior_mass_pcts)
                run.save_array("prior_intensity_pcts", last.post.prior_intensity_pcts)
            if last.post.evidence is not None:
                run.save_array("evidence_counts", last.post.evidence)
            if last.observed is not None:
                run.save_array("observed_bounds", last.observed.table())      # rows: mrvsim.score.observed.ROWS
            last.pop.save(run.dir / "population")
            last.obs.save(run.dir / "observations")
    return report, run, last


def _report_json(report: ScoreReport) -> dict[str, Any]:
    d = report.headline()
    d["completeness_by_basin"] = {k: m.as_dict() for k, m in report.completeness_by_basin.items()}
    d["per_stratum_calibration"] = {k: [None if not np.isfinite(x) else float(x) for x in v] for k, v in report.per_stratum_calibration.items()}
    d["state_shares"] = {k: {s: m.as_dict() for s, m in v.items()} for k, v in report.state_counts.items()}
    d["meta"] = report.meta
    d["calibration_ok"] = report.calibration_ok()
    return json.loads(json.dumps(d, default=lambda o: None if isinstance(o, float) and not np.isfinite(o) else (float(o) if isinstance(o, np.floating) else str(o))))
