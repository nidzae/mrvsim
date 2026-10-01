"""Run store: everything the API serves is read from ``runs/<id>/`` written by the pipeline (CLAUDE.md conventions)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from mrvsim.io.config import RunConfig
from mrvsim.io.seeds import SeedTree
from mrvsim.observe.deployment import DeploymentPlan, build_plan
from mrvsim.observe.simulator import CMSLog, ObservationLog, ObservationSet
from mrvsim.pipeline import sensor_modes
from mrvsim.population import Population, load_population
from mrvsim.estimate.fast import log_width_ratio
from mrvsim.sensors import SensorLibrary, load_library
from mrvsim.sensors.library import REFERENCES_PATH, reference_keys

_REPO = Path(__file__).resolve().parents[2]
RUNS_DIR = _REPO / "runs"
STATES = ("certified", "fails", "indeterminate")


@dataclass
class LoadedRun:
    run_id: str
    dir: Path
    config: RunConfig
    manifest: dict[str, Any]
    summary: dict[str, Any]
    pop: Population
    post_mass: np.ndarray            # (5, n) p05, p10, p50, p90, p95
    post_int: np.ndarray
    true_mass: np.ndarray
    true_int: np.ndarray
    state_mass: np.ndarray
    state_int: np.ndarray
    obs: ObservationSet
    plan: DeploymentPlan
    library: SensorLibrary
    # optional, present for runs scored after 2026-10-01 (DECISION_LOG "certification is the compliance decision at 95 %")
    post_mass_q: np.ndarray | None = None    # (101, n) posterior quantile grid, percent 0..100
    post_int_q: np.ndarray | None = None
    prior_mass: np.ndarray | None = None     # (3, n) prior p05, p50, p95
    prior_int: np.ndarray | None = None
    evidence: np.ndarray | None = None       # (3, n) usable snapshots, survey visits, CMS usable hours

    def prior_ratio(self, kpi: str) -> np.ndarray:
        """Posterior ln(p95/p05) over prior ln(p95/p05); nan where unavailable (PRD section 5.4a)."""
        post = self.post_int if kpi == "intensity" else self.post_mass
        pr = self.prior_int if kpi == "intensity" else self.prior_mass
        if pr is None:
            return np.full(post.shape[1], np.nan)
        return log_width_ratio(post[0], post[4], pr[0], pr[2])

    def prior_only(self, kpi: str) -> np.ndarray:
        ratio = self.prior_ratio(kpi)
        r_max = float(self.config.scoring.get("prior_only_ratio", 0.9))
        flag = np.isfinite(ratio) & (ratio > r_max)
        if self.evidence is not None:
            flag |= self.evidence.sum(axis=0) == 0
        return flag


def list_runs(root: Path = RUNS_DIR) -> list[dict[str, Any]]:
    out = []
    for d in sorted(root.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            m = json.loads(d.read_text())
        except Exception:  # noqa: BLE001
            continue
        has_pop = (d.parent / "population" / "facilities.npz").exists() and (d.parent / "observations" / "observations.npz").exists()
        out.append({"run_id": m["run_id"], "name": m.get("name"), "status": m.get("status"), "started_utc": m.get("started_utc"),
                    "elapsed_s": m.get("elapsed_s"), "has_summary": (d.parent / "summary.json").exists() and has_pop,
                    "summary_only": (d.parent / "summary.json").exists() and not has_pop})   # validation/optimizer internal runs keep no population
    return out


@lru_cache(maxsize=4)
def load_run(run_id: str, root: str = str(RUNS_DIR)) -> LoadedRun:
    d = Path(root) / run_id
    if not (d / "summary.json").exists():
        raise FileNotFoundError(f"run {run_id} has no summary.json (not finished?)")
    if not (d / "population" / "facilities.npz").exists():
        raise FileNotFoundError(f"run {run_id} kept only its summary (an internal validation/optimizer run); pick a run started from Sensors -> Run")
    manifest = json.loads((d / "manifest.json").read_text()); summary = json.loads((d / "summary.json").read_text())
    import yaml

    cfg = RunConfig.from_dict(yaml.safe_load((d / "config.yaml").read_text()))
    lib = load_library()
    pop = load_population(d / "population")
    log = ObservationLog.load(d / "observations" / "observations.npz")
    cms = {p.stem[4:]: CMSLog.load(p) for p in (d / "observations").glob("cms_*.npz")}
    obs = ObservationSet(cfg.year, log, cms, {}, {})
    # the last replication's plan is deterministic from config + seeds (rep = R - 1)
    R = int(summary.get("meta", {}).get("replications") or cfg.replications)
    rs = SeedTree(cfg.seed).child(rep=R - 1)
    plan = build_plan(cfg.policy, rs, pop.n_facilities, pop.lon, pop.throughput.gas_mkt_m3_yr, sensor_modes(lib), cfg.year, basin_idx=pop.basin_idx)
    opt = lambda name: np.load(d / f"{name}.npy") if (d / f"{name}.npy").exists() else None  # noqa: E731
    return LoadedRun(run_id, d, cfg, manifest, summary, pop, np.load(d / "posterior_mass_pcts.npy"), np.load(d / "posterior_intensity_pcts.npy"),
                     np.load(d / "true_mass_kg_yr.npy"), np.load(d / "true_intensity.npy"), np.load(d / "state_mass.npy"), np.load(d / "state_intensity.npy"), obs, plan, lib,
                     post_mass_q=opt("posterior_mass_quantiles"), post_int_q=opt("posterior_intensity_quantiles"),
                     prior_mass=opt("prior_mass_pcts"), prior_int=opt("prior_intensity_pcts"), evidence=opt("evidence_counts"))


def facilities_geojson(run: LoadedRun, kpi: str = "intensity") -> dict[str, Any]:
    """Facility points with three-state colouring and interval values for the map (PRD F9)."""
    pop = run.pop
    post = run.post_int if kpi == "intensity" else run.post_mass
    truth = run.true_int if kpi == "intensity" else run.true_mass
    states = run.state_int if kpi == "intensity" else run.state_mass
    q = run.post_int_q if kpi == "intensity" else run.post_mass_q
    ratio = run.prior_ratio(kpi); po = run.prior_only(kpi)
    ev = run.evidence
    basins = list(pop.strata.basins); ftypes = list(pop.strata.facility_types)
    feats = []
    fin = lambda x: None if not np.isfinite(x) else float(x)  # noqa: E731
    for i in range(pop.n_facilities):
        if not np.isfinite(post[2, i]):
            continue
        props = {"id": int(i), "state": STATES[int(states[i])] if states[i] >= 0 else "unscored", "basin": basins[int(pop.basin_idx[i])],
                 "facility_type": ftypes[int(pop.ftype_idx[i])], "p05": float(post[0, i]), "p10": float(post[1, i]), "p50": float(post[2, i]),
                 "p90": float(post[3, i]), "p95": float(post[4, i]), "truth": float(truth[i]), "scale": 1.0 if kpi == "intensity" else 1e-3,
                 # evidence (PRD section 5.4a): prior-width ratio, usable observations, prior-only flag
                 "prior_ratio": fin(ratio[i]), "prior_only": bool(po[i]),
                 "n_obs": int(ev[0, i] + ev[1, i]) if ev is not None else None, "cms_h": int(ev[2, i]) if ev is not None else None}
        if q is not None:
            props["q"] = [float(v) for v in q[::5, i]]   # 21-point quantile grid (0, 5, ..., 100 %) for P(K <= B) at any bar
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(pop.lon[i]), float(pop.lat[i])]}, "properties": props})
    return {"type": "FeatureCollection", "features": feats, "kpi": kpi, "units": "fraction" if kpi == "intensity" else "t/yr (values in kg/yr x scale)",
            "has_quantiles": q is not None, "has_evidence": run.prior_mass is not None}


def slices(run: LoadedRun, kpi: str = "intensity") -> dict[str, list[dict[str, Any]]]:
    """Slice tables by basin, facility type, and true-rate bin (PRD F10)."""
    pop = run.pop
    states = run.state_int if kpi == "intensity" else run.state_mass
    post = run.post_int if kpi == "intensity" else run.post_mass
    truth = run.true_int if kpi == "intensity" else run.true_mass
    scored = np.isfinite(post[2])
    with np.errstate(divide="ignore", invalid="ignore"):
        width = np.where(post[2] > 0, (post[4] - post[0]) / (2 * post[2]), np.nan)
    covered = (post[0] <= truth) & (truth <= post[4])
    w_max = float(run.config.scoring.get("w_max", 0.30))
    precise = width <= w_max; po = run.prior_only(kpi)
    w_thr = pop.stratum_weights("throughput")
    rate_bins = [0, 1, 10, 100, 1e9]; rate = run.true_mass / 8760.0
    labels = ["<1 kg/h", "1-10 kg/h", "10-100 kg/h", ">100 kg/h"]
    groups = {"basin": (list(pop.strata.basins), pop.basin_idx), "facility_type": (list(pop.strata.facility_types), pop.ftype_idx),
              "rate_bin": (labels, np.clip(np.digitize(rate, rate_bins[1:-1]), 0, 3))}
    out: dict[str, list[dict[str, Any]]] = {}
    for name, (keys, idx) in groups.items():
        rows = []
        for k, key in enumerate(keys):
            m = scored & (idx == k)
            if not m.any():
                continue
            rows.append({"group": key, "n": int(m.sum()), "certified_share": float((states[m] == 0).mean()), "fails_share": float((states[m] == 1).mean()),
                         "indeterminate_share": float((states[m] == 2).mean()), "certified_share_throughput": float(w_thr[m][states[m] == 0].sum() / max(w_thr[m].sum(), 1e-12)),
                         "width_median": float(np.nanmedian(width[m])), "coverage": float(covered[m].mean()),
                         "precise_share": float(precise[m].mean()), "certified_prior_only_share": float(((states[m] == 0) & po[m]).mean())})
        out[name] = rows
    return out


def facility_detail(run: LoadedRun, fid: int, bar_mass_t: float | None, bar_intensity: float | None) -> dict[str, Any]:
    """Drill-down: interval vs bar, observation timeline, truth (oracle, labelled) (PRD F9)."""
    pop, log = run.pop, run.obs.log
    m = log.facility_idx == fid
    keys = list(log.sensor_keys)
    timeline = [{"hour": int(h), "day": float(h / 24.0), "sensor": keys[int(s)], "usable": bool(u), "cloud_blocked": bool(c), "sun_blocked": bool(sb), "wind_blocked": bool(wb),
                 "detected": bool(d), "reported_kg_h": None if not np.isfinite(r) else float(r), "false_positive": bool(fp), "source_idx": int(si),
                 "incidental": bool(inc), "scene_target": int(st) if inc else None}
                for h, s, u, c, sb, wb, d, r, fp, si, inc, st in zip(log.hour_idx[m], log.sensor_idx[m], log.usable[m], log.cloud_blocked[m], log.sun_blocked[m], log.wind_blocked[m],
                                                                    log.detected[m], log.reported_kg_h[m], log.oracle_false_positive[m], log.source_idx[m], log.incidental[m], log.scene_target_idx[m])]
    timeline.sort(key=lambda r: r["hour"])
    cms = {}
    for key, c in run.obs.cms.items():
        where = np.nonzero(c.facilities == fid)[0]
        if where.size:
            i = int(where[0]); det = c.detected[i]; us = c.usable[i]
            # daily aggregates for the timeline
            days = det.reshape(365, 24).sum(axis=1); usd = us.reshape(365, 24).sum(axis=1)
            cms[key] = {"detected_hours_by_day": days.tolist(), "usable_hours_by_day": usd.tolist(), "detected_share": float(det[us].mean()) if us.any() else None}
    s0, s1 = pop.source_offset[fid], pop.source_offset[fid + 1]
    truth_sources = [{"q_kg_h": float(pop.q_kg_h[j]), "intermittent": bool(pop.z[j] == 1), "pi": float(pop.pi[j]), "on_hours": int(pop.states.on_hours()[j])} for j in range(s0, s1)]
    def kpi_block(post, q, pr, truth, scale, bar):
        b = {"p05": float(post[0, fid] * scale), "p10": float(post[1, fid] * scale), "p50": float(post[2, fid] * scale), "p90": float(post[3, fid] * scale),
             "p95": float(post[4, fid] * scale), "truth": float(truth[fid] * scale), "bar": bar}
        if q is not None:
            b["quantiles"] = [float(v * scale) for v in q[:, fid]]   # percent 0..100 in steps of 1
        if pr is not None:
            b["prior"] = {"p05": float(pr[0, fid] * scale), "p50": float(pr[1, fid] * scale), "p95": float(pr[2, fid] * scale)}
        return b

    # which sensors cover this facility, under which policy rule (PRD F9; the plan is rebuilt from config + seeds at load)
    gas = pop.throughput.gas_mkt_m3_yr
    order = np.argsort(-gas, kind="stable"); rank = int(np.nonzero(order == fid)[0][0]) + 1
    n_fac = int(pop.n_facilities)
    throughput = {"gas_mkt_m3_yr": float(gas[fid]), "mmbtu_yr": float(pop.throughput.mmbtu_yr[fid]), "rank": rank, "n_facilities": n_fac,
                  "top_share": rank / n_fac, "throughput_class": int(pop.tclass_idx[fid]), "ghgrp_reporter": bool(pop.throughput.ghgrp_reporter[fid])}
    monitoring = []
    for key, spec in (run.config.policy.get("sensors") or {}).items():
        if not spec.get("enabled", True):
            continue
        try:
            sensor = run.library[key]
        except KeyError:
            continue
        dep = run.plan.deployments.get(key)
        covered = bool(dep is not None and dep.facilities.size and dep.facilities[np.searchsorted(dep.facilities, fid) % dep.facilities.size] == fid)
        cov = float(spec.get("coverage", 1.0)); targeting = str(spec.get("targeting", "random")); freq = int(spec.get("frequency_per_year", 1))
        tasked = bool(sensor.orbit.tasked) if sensor.orbit else False
        if cov >= 1.0:
            rule = "all facilities"
        elif targeting == "random":
            rule = f"random {cov:.0%} of facilities"
        else:
            n_sel = int(round(cov * n_fac))
            rule = f"top {cov:.0%} by gas throughput (ranks 1–{n_sel} of {n_fac})"
        if sensor.schedule in ("campaign", "survey"):
            sched = str(spec.get("scheduling", "independent"))
            how = (f"{freq} visit{'s' if freq != 1 else ''}/yr, regional campaign of {int(spec.get('campaign_days', 5))} days per basin" if sched == "campaign"
                   else f"{freq} visit{'s' if freq != 1 else ''}/yr on independent dates")
        elif sensor.schedule == "orbit":
            how = f"{freq} tasked overpasses/yr" if tasked else "every daytime overpass"
        else:
            how = "continuous, every hour"
        days = sorted(int(h) // 24 for h in dep.visit_hours[dep.visit_facility == fid]) if (covered and dep.visit_hours is not None) else []
        reason = ("selected" if covered else "not selected") if cov < 1.0 else "covered"
        if cov < 1.0 and targeting != "random":
            reason += f" (rank {rank} of {n_fac})"
        si_log = keys.index(key) if key in keys else -1
        inc_rows = m & (log.sensor_idx == si_log) & log.incidental if si_log >= 0 else np.zeros_like(m)
        inc_targets = sorted({int(t) for t in log.scene_target_idx[inc_rows]})
        monitoring.append({"sensor": key, "schedule": sensor.schedule, "tasked": tasked, "covered": covered, "coverage": cov, "targeting": targeting,
                           "incidental_looks": int(inc_rows.sum()), "incidental_usable": int((inc_rows & log.usable).sum()), "incidental_from": inc_targets[:12],
                           "frequency_per_year": freq, "scheduling": spec.get("scheduling", "independent") if sensor.schedule in ("campaign", "survey") else None,
                           "rule": rule, "how": how, "reason": reason, "planned_visit_days": days})
    ev = run.evidence
    evidence = {"n_usable_snapshots": int(ev[0, fid]), "n_survey_visits": int(ev[1, fid]), "cms_usable_hours": int(ev[2, fid]),
                "n_incidental_usable": int((m & log.incidental & log.usable & (log.source_idx < 0)).sum()),
                "prior_ratio_mass": None if not np.isfinite(run.prior_ratio("mass")[fid]) else float(run.prior_ratio("mass")[fid]),
                "prior_ratio_intensity": None if not np.isfinite(run.prior_ratio("intensity")[fid]) else float(run.prior_ratio("intensity")[fid]),
                "prior_only_ratio": float(run.config.scoring.get("prior_only_ratio", 0.9))} if ev is not None else None
    return {"id": fid, "lat": float(pop.lat[fid]), "lon": float(pop.lon[fid]), "basin": list(pop.strata.basins)[int(pop.basin_idx[fid])],
            "facility_type": list(pop.strata.facility_types)[int(pop.ftype_idx[fid])], "n_sources": int(pop.n_sources[fid]),
            "mass_t_yr": kpi_block(run.post_mass, run.post_mass_q, run.prior_mass, run.true_mass, 1e-3, bar_mass_t),
            "intensity": kpi_block(run.post_int, run.post_int_q, run.prior_int, run.true_int, 1.0, bar_intensity),
            "evidence": evidence, "w_max": float(run.config.scoring.get("w_max", 0.30)), "throughput": throughput, "monitoring": monitoring,
            "timeline": timeline, "cms": cms, "oracle_truth_sources": truth_sources, "method": run.summary.get("meta", {}).get("sampler", "fast")}


def references_table(path: Path = REFERENCES_PATH) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*([a-z0-9\-]+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*([^|]+?)\s*\|\s*$", line)
        if m and m.group(1) != "key":
            out[m.group(1)] = {"citation": m.group(2), "doi_or_url": m.group(3), "status": m.group(4)}
    return out


def attribution(run: LoadedRun) -> dict[str, Any]:
    """Every citation key that contributed to the run, resolved against REFERENCES.md, verify-status flagged (PRD F13)."""
    refs = references_table()
    keys = set(run.summary.get("meta", {}).get("citation_keys") or [])
    keys |= set(run.pop.citation_keys())
    sensors = list(run.cfg_sensor_keys()) if hasattr(run, "cfg_sensor_keys") else list((run.config.policy.get("sensors") or {}).keys())
    keys |= set(run.library.citation_keys(sensors))
    items = []
    for k in sorted(keys):
        r = refs.get(k)
        items.append({"key": k, "resolved": r is not None, "citation": r["citation"] if r else None, "doi_or_url": r["doi_or_url"] if r else None,
                      "status": r["status"] if r else "UNRESOLVED", "flag": (r is None) or ("verify" in r["status"])})
    sensor_blocks = [{"sensor": s, "unverified_blocks": list(run.library[s].unverified_blocks()), "tier": run.library[s].tier} for s in sensors]
    return {"run_id": run.run_id, "items": items, "n_flagged": sum(i["flag"] for i in items), "sensor_provenance": sensor_blocks,
            "priors_provenance": run.pop.priors.provenance, "strata_provenance": run.pop.strata.provenance}
