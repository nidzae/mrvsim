"""API tests (CLAUDE.md Phase 8) with FastAPI's TestClient; a tiny run is executed through the job registry."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from mrvsim.api import server, store


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    store.RUNS_DIR = tmp_path_factory.mktemp("runs")   # isolate from the real runs/ directory
    return TestClient(server.app)


def _wait(client: TestClient, job_id: str, timeout_s: float = 600) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "error"):
            assert j["status"] == "done", j["error"]
            return j["result"]
        time.sleep(1.0)
    raise TimeoutError(job_id)


def test_health_sensors_quickstart_references(client: TestClient) -> None:
    assert client.get("/api/health").json()["ok"] is True
    s = client.get("/api/sensors").json()
    assert {x["key"] for x in s["sensors"]} >= {"bridger_gml", "tropomi", "cms_generic"}
    assert "default_policy" in s
    assert "MRVSim Quick Start" in client.get("/api/quickstart").json()["markdown"]
    refs = client.get("/api/references").json()["references"]
    assert refs["cusworth2022"]["status"] == "ok"


@pytest.mark.slow
def test_run_summary_facility_attribution(client: TestClient) -> None:
    body = {"name": "api-test", "mode": "custom", "seed": 3, "replications": 1, "n_draws": 300, "facilities_per_stratum": 2, "n_per_stratum": 30,
            "policy": {"sensors": {"bridger_gml": {"coverage": 1.0, "frequency_per_year": 2}, "cms_generic": {"coverage": 0.2, "targeting": "throughput"}}},
            "scoring": {"bar_mass_t_yr": 50.0, "bar_intensity": 0.002, "w_max": 0.3}}
    job = client.post("/api/run", json=body).json()
    res = _wait(client, job["job_id"])
    run_id = res["run_id"]
    assert res["headline"]["intensity"]["calibration"]["n"] == 1
    assert any(r["run_id"] == run_id for r in client.get("/api/runs").json()["runs"])
    s = client.get(f"/api/run/{run_id}/summary?kpi=intensity").json()
    assert s["slices"]["basin"] and "certified_share" in s["slices"]["basin"][0]
    g = client.get(f"/api/run/{run_id}/facilities?kpi=mass").json()
    assert g["type"] == "FeatureCollection" and g["features"]
    props = g["features"][0]["properties"]
    assert set(props) >= {"id", "state", "p05", "p10", "p50", "p90", "p95", "truth", "basin", "facility_type"}
    assert props["state"] in ("certified", "fails", "indeterminate")
    assert len(props["q"]) == 21 and props["prior_ratio"] is not None and props["n_obs"] is not None and isinstance(props["prior_only"], bool)
    fid = props["id"]
    d = client.get(f"/api/run/{run_id}/facility/{fid}").json()
    assert d["id"] == fid and "timeline" in d and d["mass_t_yr"]["p50"] > 0 and d["intensity"]["bar"] == 0.002
    assert len(d["intensity"]["quantiles"]) == 101 and d["intensity"]["prior"]["p95"] > d["intensity"]["prior"]["p05"]
    assert d["evidence"]["n_usable_snapshots"] >= 0 and d["w_max"] == 0.3
    mon = {m["sensor"]: m for m in d["monitoring"]}
    assert set(mon) == {"bridger_gml", "cms_generic"} and mon["bridger_gml"]["covered"] and mon["bridger_gml"]["rule"] == "all facilities"
    assert len(mon["bridger_gml"]["planned_visit_days"]) == 2 and mon["cms_generic"]["rule"].startswith("top 20%")
    assert 1 <= d["throughput"]["rank"] <= d["throughput"]["n_facilities"]
    assert any(t["sensor"] == "bridger_gml" for t in d["timeline"])
    a = client.get(f"/api/run/{run_id}/attribution").json()
    keys = {i["key"] for i in a["items"]}
    assert {"ghgrp", "johnson2021", "cost-assumptions"} <= keys
    assert a["priors_provenance"] == "FITTED" and all(i["resolved"] for i in a["items"])
    v = client.get("/api/validation").json()
    assert "targets" in v and v["priors_placeholder"] is False
    bj = client.post(f"/api/run/{run_id}/facility/{fid}/budget?n_draws=300").json()
    vb = _wait(client, bj["job_id"])
    assert set(vb["shares"]) == {"quantification", "temporal_sampling", "detection_censoring", "spatial_completeness", "false_calls", "denominator"}
    r = client.post("/api/gap-analysis", json={"run_id": run_id, "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code in (401, 429, 502)


def test_unknown_job_and_run(client: TestClient) -> None:
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/run/nope/summary").status_code == 404


def test_modes_and_estimate(client: TestClient) -> None:
    q = client.post("/api/estimate", json={"mode": "quick"}).json()
    f = client.post("/api/estimate", json={"mode": "full"}).json()
    c = client.post("/api/estimate", json={"mode": "custom", "replications": 1, "n_draws": 500, "facilities_per_stratum": 2, "n_per_stratum": 30}).json()
    assert q["facilities_estimated"] == 630 and q["replications"] == 3 and q["n_draws"] == 2000
    assert f["facilities_estimated"] == 6300 and f["n_draws"] == 10000 and f["estimated_seconds"] > q["estimated_seconds"] * 5
    assert c["facilities_estimated"] == 126 and c["replications"] == 1
