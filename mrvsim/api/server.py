"""FastAPI server (CLAUDE.md Phase 8): run / score / drill-down / attribution / validation / optimize / gap analysis.

Run with::

    .venv/bin/uvicorn mrvsim.api.server:app --reload --port 8000

Long operations return a job id; poll ``GET /api/jobs/{id}``. Interactive runs default to
PRD N3 settings: 10 facilities per stratum, 2,000 draws, 3 replications.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from mrvsim.api import store
from mrvsim.api.jobs import JobRegistry
from mrvsim.io.config import RunConfig, load_config
from mrvsim.sensors import load_library

_REPO = Path(__file__).resolve().parents[2]
app = FastAPI(title="MRVSim API", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
JOBS = JobRegistry(workers=2)
LIB = load_library()
DEFAULT_POLICY = {"sensors": {"bridger_gml": {"coverage": 1.0, "frequency_per_year": 2}, "ghgsat_c": {"coverage": 0.3, "frequency_per_year": 12, "targeting": "throughput"},
                              "tropomi": {"coverage": 1.0}}}


# ----------------------------------------------------------------------------- models
class RunRequest(BaseModel):
    name: str = "interactive"
    seed: int = 20260930
    policy: dict[str, Any] = Field(default_factory=lambda: DEFAULT_POLICY)
    scoring: dict[str, Any] = Field(default_factory=lambda: {"bar_mass_t_yr": 50.0, "bar_intensity": 0.002, "w_max": 0.30})
    replications: int = 3
    n_draws: int = 2000
    facilities_per_stratum: int | None = 10
    n_per_stratum: int = 30


class OptimizeRequest(BaseModel):
    seed: int = 20260930
    sensors: list[str] = ["bridger_gml", "ghgsat_c", "cms_generic"]
    fixed: dict[str, dict[str, Any]] = Field(default_factory=lambda: {"tropomi": {"coverage": 1.0}})
    n_trials: int = 12
    kappa_min: float = 0.85
    w_max: float = 0.30
    theta: float = 0.5
    kpi: str = "intensity"
    replications_trial: int = 1
    replications_full: int = 2
    n_draws: int = 1000
    facilities_per_stratum: int = 5
    scoring: dict[str, Any] = Field(default_factory=lambda: {"bar_mass_t_yr": 50.0, "bar_intensity": 0.002, "w_max": 0.30})


class ChatMessage(BaseModel):
    role: str
    content: str


class GapRequest(BaseModel):
    run_id: str
    messages: list[ChatMessage]
    tornado_job_id: str | None = None


# ----------------------------------------------------------------------------- helpers
def _cfg_from_request(req: RunRequest) -> RunConfig:
    return RunConfig.from_dict({"name": req.name, "seed": req.seed, "replications": req.replications, "population": {"n_per_stratum": req.n_per_stratum},
                                "policy": req.policy, "estimator": {"n_draws": req.n_draws}, "scoring": req.scoring})


def _run_job(req: RunRequest):
    from mrvsim.pipeline import run_scored

    def fn(job):
        cfg = _cfg_from_request(req)
        job.progress = {"stage": "running", "replications": req.replications}
        report, run, _ = run_scored(cfg, root=store.RUNS_DIR, replications=req.replications, n_draws=req.n_draws,
                                    facilities_per_stratum=req.facilities_per_stratum, library=LIB)
        store.load_run.cache_clear()
        return {"run_id": run.run_id, "headline": report.headline(), "calibration_ok": report.calibration_ok(), "elapsed_s": run.manifest.elapsed_s}

    return fn


def _tornado(run_id: str, req: RunRequest):
    """One-at-a-time sensor changes (PRD F10): each deployed sensor off, doubled frequency, and each absent sensor added."""
    from mrvsim.pipeline import run_scored

    def fn(job):
        base = store.load_run(run_id, str(store.RUNS_DIR))
        base_w = base.summary["intensity"]["width_median"]["mean"]; base_cert = base.summary["intensity"]["certified_share_weighted_throughput"]["mean"]
        base_cost = base.summary["cost"]["cost_total_usd"]["mean"]
        sensors = dict(base.config.policy.get("sensors") or {})
        variants: dict[str, dict[str, Any]] = {}
        for k, v in sensors.items():
            variants[f"{k}: off"] = {kk: vv for kk, vv in sensors.items() if kk != k}
            if LIB[k].schedule in ("campaign", "survey", "orbit") and (LIB[k].orbit is None or LIB[k].orbit.tasked):
                variants[f"{k}: 2x frequency"] = {**sensors, k: {**v, "frequency_per_year": min(52, 2 * int(v.get("frequency_per_year", 1)))}}
            variants[f"{k}: full coverage"] = {**sensors, k: {**v, "coverage": 1.0}}
        for k in ("cms_generic", "bridger_gml", "ghgsat_c", "ogi"):
            if k not in sensors:
                variants[f"{k}: add (20%)"] = {**sensors, k: {"coverage": 0.2, "frequency_per_year": 12 if k == "ghgsat_c" else 1, "targeting": "throughput"}}
        rows = []
        for i, (label, pol) in enumerate(variants.items()):
            job.progress = {"stage": label, "done": i, "total": len(variants)}
            cfg = base.config.with_overrides(name=f"tornado-{label}", policy={"sensors": pol}, replications=req.replications)
            rep, _, _ = run_scored(cfg, root=store.RUNS_DIR, replications=req.replications, n_draws=req.n_draws, facilities_per_stratum=req.facilities_per_stratum,
                                   library=LIB, keep_last=False)
            rows.append({"change": label, "width_median": rep.kpi["intensity"]["width_median"].mean, "delta_width": rep.kpi["intensity"]["width_median"].mean - base_w,
                         "certified_share_throughput": rep.kpi["intensity"]["certified_share_weighted_throughput"].mean,
                         "delta_certified": rep.kpi["intensity"]["certified_share_weighted_throughput"].mean - base_cert,
                         "cost_total_usd": rep.cost["cost_total_usd"].mean, "delta_cost": rep.cost["cost_total_usd"].mean - base_cost})
        rows.sort(key=lambda r: r["delta_width"])
        out = {"run_id": run_id, "base": {"width_median": base_w, "certified_share_throughput": base_cert, "cost_total_usd": base_cost}, "rows": rows}
        (base.dir / "tornado.json").write_text(json.dumps(out, indent=2))
        return out

    return fn


# ----------------------------------------------------------------------------- endpoints
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "runs_dir": str(store.RUNS_DIR), "anthropic_key_configured": bool(os.environ.get("ANTHROPIC_API_KEY"))}


@app.get("/api/sensors")
def sensors() -> dict[str, Any]:
    return {"sensors": [{"key": s.key, "name": s.name, "class": s.sensor_class, "tier": s.tier, "enabled_for_certification": s.enabled_for_certification,
                         "schedule": s.schedule, "pod50_kg_h": s.pod.pod50_kg_h, "pod90_kg_h": s.pod.pod90_kg_h, "unverified_blocks": list(s.unverified_blocks()),
                         "cost": {"per_site_visit_usd": s.cost.per_site_visit_usd, "per_tasking_usd": s.cost.per_tasking_usd, "per_site_year_usd": s.cost.per_site_year_usd}}
                        for s in LIB], "default_policy": DEFAULT_POLICY}


@app.get("/api/runs")
def runs() -> dict[str, Any]:
    return {"runs": store.list_runs(store.RUNS_DIR)}


@app.post("/api/run")
def post_run(req: RunRequest) -> dict[str, Any]:
    job = JOBS.submit("run", _run_job(req))
    return job.to_dict()


@app.get("/api/jobs/{job_id}")
def job(job_id: str) -> dict[str, Any]:
    j = JOBS.get(job_id)
    if j is None:
        raise HTTPException(404, "unknown job")
    return j.to_dict()


@app.get("/api/run/{run_id}/summary")
def summary(run_id: str, kpi: str = "intensity") -> dict[str, Any]:
    try:
        run = store.load_run(run_id, str(store.RUNS_DIR))
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    tornado = json.loads((run.dir / "tornado.json").read_text()) if (run.dir / "tornado.json").exists() else None
    return {"run_id": run_id, "config": run.config.to_dict(), "summary": run.summary, "slices": store.slices(run, kpi), "tornado": tornado,
            "manifest": {k: run.manifest.get(k) for k in ("seed", "git", "elapsed_s", "started_utc", "versions")}}


@app.get("/api/run/{run_id}/facilities")
def facilities(run_id: str, kpi: str = "intensity") -> dict[str, Any]:
    try:
        return store.facilities_geojson(store.load_run(run_id, str(store.RUNS_DIR)), kpi)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/api/run/{run_id}/facility/{fid}")
def facility(run_id: str, fid: int) -> dict[str, Any]:
    run = store.load_run(run_id, str(store.RUNS_DIR))
    if fid < 0 or fid >= run.pop.n_facilities:
        raise HTTPException(404, "unknown facility")
    sc = run.config.scoring
    return store.facility_detail(run, fid, float(sc.get("bar_mass_t_yr", 50.0)), float(sc.get("bar_intensity", 0.002)))


@app.post("/api/run/{run_id}/facility/{fid}/budget")
def facility_budget(run_id: str, fid: int, n_draws: int = 2000) -> dict[str, Any]:
    from mrvsim.estimate.budget import variance_budget
    from mrvsim.io.seeds import SeedTree

    run = store.load_run(run_id, str(store.RUNS_DIR))
    R = int(run.summary.get("meta", {}).get("replications") or run.config.replications)
    seeds = SeedTree(run.config.seed).child(rep=R - 1)

    def fn(job):
        vb = variance_budget(fid, run.pop, run.obs, run.plan, LIB, seeds, n_draws=n_draws)
        return {"facility": fid, "total_width_kg_yr": vb.total_width, "shares": vb.shares, "reductions": vb.reductions, "widths": vb.widths, "meta": vb.meta}

    return JOBS.submit("budget", fn).to_dict()


@app.post("/api/run/{run_id}/tornado")
def tornado(run_id: str, req: RunRequest) -> dict[str, Any]:
    return JOBS.submit("tornado", _tornado(run_id, req)).to_dict()


@app.get("/api/run/{run_id}/attribution")
def attribution(run_id: str) -> dict[str, Any]:
    return store.attribution(store.load_run(run_id, str(store.RUNS_DIR)))


@app.get("/api/references")
def references() -> dict[str, Any]:
    return {"references": store.references_table()}


@app.get("/api/quickstart")
def quickstart() -> dict[str, str]:
    return {"markdown": (_REPO / "docs" / "QUICKSTART.md").read_text(encoding="utf-8")}


@app.get("/api/validation")
def validation() -> dict[str, Any]:
    from mrvsim.validate.runner import latest_results_path
    from mrvsim.validate.targets import load_targets

    p = latest_results_path()
    results = json.loads(p.read_text()) if p else {"results": []}
    return {"latest": results, "path": str(p) if p else None, "targets": load_targets(),
            "priors_placeholder": store.load_library is not None and _priors_placeholder()}


def _priors_placeholder() -> bool:
    from mrvsim.population import load_priors, load_strata
    from mrvsim.population.priors import DEFAULT_PRIORS_PATH

    st = load_strata()
    return load_priors(DEFAULT_PRIORS_PATH, list(st.basins), list(st.facility_types)).is_placeholder


@app.post("/api/validation/run")
def validation_run(quick: bool = True, allow_placeholder: bool = False) -> dict[str, Any]:
    from mrvsim.validate.runner import run_all

    def fn(job):
        if allow_placeholder:
            os.environ["MRVSIM_ALLOW_PLACEHOLDER_VALIDATION"] = "1"
        results, path = run_all(quick=quick)
        return {"path": str(path), "results": [r.to_dict() for r in results]}

    return JOBS.submit("validation", fn).to_dict()


@app.post("/api/optimize")
def optimize(req: OptimizeRequest) -> dict[str, Any]:
    from mrvsim.policy import SearchSpace, SensorPolicy, optimize as run_opt

    def fn(job):
        cfg = RunConfig.from_dict({"name": "optimize", "seed": req.seed, "replications": req.replications_full, "population": {"n_per_stratum": 30},
                                   "estimator": {"n_draws": req.n_draws}, "scoring": req.scoring})
        space = SearchSpace(sensors=tuple(req.sensors), fixed={k: SensorPolicy(**v) for k, v in req.fixed.items()})
        res = run_opt(cfg, LIB, space, n_trials=req.n_trials, kappa_min=req.kappa_min, w_max=req.w_max, theta=req.theta, replications_trial=req.replications_trial,
                      replications_full=req.replications_full, n_draws=req.n_draws, facilities_per_stratum=req.facilities_per_stratum, root=store.RUNS_DIR, kpi=req.kpi)
        return res.to_dict()

    return JOBS.submit("optimize", fn).to_dict()


# ----------------------------------------------------------------------------- gap analysis (PRD F11)
GAP_SYSTEM = """You are the gap-analysis assistant inside MRVSim, a methane MRV coverage simulator (an OSSE over synthetic US onshore
oil and gas facilities). You see the current run's headline metrics, slice tables, variance budget (if computed), and tornado
chart (if computed). Help the user understand why facilities are indeterminate and which sensor change would help most.
Ground every claim in the numbers provided; say when something was not computed. Intervals are two-sided 90 % credible
intervals; certification uses the one-sided 90 % upper bound against the bar B with precision w_max.
When you propose a configuration, end your reply with a fenced ```yaml block containing ONLY a policy of the form
sensors: {<sensor_key>: {coverage: <0-1>, frequency_per_year: <int>, targeting: random|throughput}} using sensor keys from the list.
Keep replies under 300 words."""


def _gap_context(run_id: str) -> str:
    run = store.load_run(run_id, str(store.RUNS_DIR))
    ctx = {"run_id": run_id, "policy": run.config.policy, "scoring": run.config.scoring, "headline": run.summary, "slices": store.slices(run),
           "sensor_keys": [s.key for s in LIB], "priors_provenance": run.pop.priors.provenance}
    if (run.dir / "tornado.json").exists():
        ctx["tornado"] = json.loads((run.dir / "tornado.json").read_text())
    return json.dumps(ctx, default=lambda o: None if isinstance(o, float) and not np.isfinite(o) else str(o))[:60000]


@app.post("/api/gap-analysis")
def gap_analysis(req: GapRequest) -> dict[str, Any]:
    """Server-side proxy: the key comes from ANTHROPIC_API_KEY (or an `ant auth login` profile); never from code or the browser."""
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise HTTPException(500, "anthropic SDK not installed") from e
    system = [{"type": "text", "text": GAP_SYSTEM, "cache_control": {"type": "ephemeral"}},
              {"type": "text", "text": "Current run context (JSON):\n" + _gap_context(req.run_id)}]
    messages = [{"role": m.role, "content": m.content} for m in req.messages]
    try:
        client = anthropic.Anthropic()   # credentials from ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / `ant auth login`; never in code
        resp = client.beta.messages.create(model="claude-opus-5-5", max_tokens=4000, system=system, messages=messages,
                                           betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    except anthropic.AuthenticationError as e:
        raise HTTPException(401, "Anthropic authentication failed: set ANTHROPIC_API_KEY or run `ant auth login`") from e
    except anthropic.RateLimitError as e:
        raise HTTPException(429, "rate limited by the Anthropic API; retry shortly") from e
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise HTTPException(502, "could not reach the Anthropic API") from e
    except anthropic.AnthropicError as e:   # e.g. no credentials configured at all
        raise HTTPException(401, f"Anthropic client error: {e}") from e
    except (TypeError, ValueError) as e:     # SDK versions that raise plain errors for a missing credential
        raise HTTPException(401, f"Anthropic credentials not configured: {e}") from e
    if resp.stop_reason == "refusal":
        return {"text": "The model declined this request.", "policy_yaml": None, "stop_reason": "refusal", "stop_details": getattr(resp, "stop_details", None) and resp.stop_details.model_dump()}
    text = "".join(b.text for b in resp.content if b.type == "text")
    policy_yaml = None
    if "```yaml" in text:
        block = text.split("```yaml", 1)[1].split("```", 1)[0]
        try:
            parsed = yaml.safe_load(block)
            if isinstance(parsed, dict) and "sensors" in parsed:
                policy_yaml = block.strip()
        except yaml.YAMLError:
            policy_yaml = None
    return {"text": text, "policy_yaml": policy_yaml, "stop_reason": resp.stop_reason, "model": resp.model}


# ----------------------------------------------------------------------------- static front end (after `npm run build`)
_DIST = _REPO / "web" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="web")
