"""Phase 0 acceptance test: two runs with the same config and seed are byte-identical.

The workload here exercises every output writer and several named streams,
standing in for the pipeline stages that later phases add. Each later phase
must keep this property; the end-to-end version of this test is re-run against
the full pipeline in ``tests/unit/test_pipeline_reproducibility.py`` once it exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from mrvsim.io.config import RunConfig, canonical_yaml, config_hash, load_config
from mrvsim.io.run import RunContext, compare_run_dirs


def _workload(run: RunContext) -> None:
    """A stand-in pipeline: several seeded stages writing every output type."""
    with run.stage("population"):
        rng = run.seeds.rng("population", "rates")
        rates = np.exp(rng.normal(0.0, 1.5, size=(50, 8)))
        run.save_array("rates_kg_h", rates)
    with run.stage("observe"):
        per_rep = [run.seeds.rng("observe", "aircraft", rep=r).random(20) for r in range(run.config.replications)]
        run.save_array("detections", np.stack(per_rep))
        frame = pd.DataFrame({"facility": np.arange(50), "mass_kg_yr": rates.sum(axis=1) * 8760.0})
        run.save_frame("truth", frame)
    with run.stage("score"):
        run.save_json("summary", {"n": 50, "mean_rate": float(rates.mean()), "seed": run.config.seed})


def test_same_seed_byte_identical(tmp_path: Path, small_config: RunConfig) -> None:
    with RunContext(small_config, root=tmp_path, run_id="a") as ra:
        _workload(ra)
    with RunContext(small_config, root=tmp_path, run_id="b") as rb:
        _workload(rb)
    result = compare_run_dirs(ra.dir, rb.dir)
    assert result, "no output files compared"
    assert all(result.values()), f"non-identical files: {[k for k, v in result.items() if not v]}"
    assert set(result) >= {"config.yaml", "rates_kg_h.npy", "detections.npy", "truth.csv", "summary.json"}


def test_different_seed_differs(tmp_path: Path, small_config: RunConfig) -> None:
    with RunContext(small_config, root=tmp_path, run_id="a") as ra:
        _workload(ra)
    with RunContext(small_config.with_overrides(seed=54321), root=tmp_path, run_id="b") as rb:
        _workload(rb)
    result = compare_run_dirs(ra.dir, rb.dir)
    assert result["rates_kg_h.npy"] is False
    assert result["config.yaml"] is False


def test_manifest_records_required_fields(tmp_path: Path, small_config: RunConfig) -> None:
    with RunContext(small_config, root=tmp_path) as run:
        _workload(run)
    m = json.loads((run.dir / "manifest.json").read_text())
    for key in ("seed", "config_sha256", "git", "elapsed_s", "started_utc", "finished_utc", "versions", "stages"):
        assert key in m, key
    assert m["seed"] == small_config.seed
    assert m["status"] == "ok"
    assert m["config_sha256"] == config_hash(small_config)
    assert set(m["stages"]) == {"population", "observe", "score"}
    assert (run.dir / "config.yaml").read_text() == canonical_yaml(small_config)


def test_manifest_records_error(tmp_path: Path, small_config: RunConfig) -> None:
    with pytest.raises(RuntimeError):
        with RunContext(small_config, root=tmp_path) as run:
            raise RuntimeError("boom")
    m = json.loads((run.dir / "manifest.json").read_text())
    assert m["status"] == "error"
    assert "boom" in m["error"]


def test_default_config_loads(repo_root: Path) -> None:
    cfg = load_config(repo_root / "configs" / "default.yaml")
    assert cfg.seed == 20260930
    assert cfg.replications == 200
    assert cfg.population["n_per_stratum"] == 100


def test_config_hash_ignores_formatting() -> None:
    a = RunConfig.from_dict({"seed": 1, "population": {"x": 1, "y": 2}})
    b = RunConfig.from_dict({"population": {"y": 2, "x": 1}, "seed": 1})
    assert config_hash(a) == config_hash(b)


def test_unknown_section_rejected() -> None:
    with pytest.raises(ValueError, match="unknown config section"):
        RunConfig.from_dict({"seed": 1, "sensor": {}})


def test_missing_seed_rejected() -> None:
    with pytest.raises(ValueError, match="seed"):
        RunConfig.from_dict({"year": 2024})
