"""Run persistence: ``runs/<id>/`` with config, seed, git SHA, elapsed time, outputs.

CLAUDE.md conventions: "Log every run's config, seed, git SHA, and elapsed
time to ``runs/<id>/``." A :class:`RunContext` owns one run directory, the
run's :class:`~mrvsim.io.seeds.SeedTree`, and a small set of output writers
that produce byte-stable files so that two runs with the same config and seed
can be compared file-for-file (the Phase 0 reproducibility test).

Layout of a run directory::

    runs/<id>/
      manifest.json      seed, config hash, git SHA, versions, timing, outputs
      config.yaml        canonical YAML of the config actually used
      <name>.npy         numpy arrays written via ``save_array``
      <name>.json        JSON documents written via ``save_json``
      <name>.csv         pandas frames written via ``save_frame``

The manifest is the only file with wall-clock content; everything else is a
pure function of (config, seed, code).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from mrvsim import __version__
from mrvsim.io.config import RunConfig, canonical_yaml, config_hash
from mrvsim.io.seeds import SeedTree

_REPO_ROOT = Path(__file__).resolve().parents[2]


def git_sha(repo: Path | None = None) -> dict[str, Any]:
    """Current commit SHA and dirty flag, or ``{"sha": None}`` outside a repo."""
    cwd = str(repo or _REPO_ROOT)
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=cwd, capture_output=True, text=True, check=True, timeout=10,
        ).stdout
        return {"sha": sha, "dirty": bool(status.strip())}
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return {"sha": None, "dirty": None}


def _versions() -> dict[str, str]:
    out = {"mrvsim": __version__, "python": platform.python_version(), "numpy": np.__version__}
    for mod in ("scipy", "pandas", "pymc", "skyfield", "optuna"):
        try:
            out[mod] = __import__(mod).__version__
        except Exception:  # noqa: BLE001 - optional at import time
            out[mod] = "not installed"
    return out


@dataclass
class RunManifest:
    run_id: str
    name: str
    seed: int
    config_sha256: str
    git: dict[str, Any]
    versions: dict[str, str]
    platform: str
    started_utc: str
    finished_utc: str | None = None
    elapsed_s: float | None = None
    status: str = "running"
    outputs: dict[str, str] = field(default_factory=dict)
    stages: dict[str, float] = field(default_factory=dict)
    error: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


class RunContext:
    """One simulation run: directory, seed tree, manifest, output writers.

    Use as a context manager so that the manifest records elapsed time and
    status even when a stage raises::

        with RunContext(cfg, root="runs") as run:
            rng = run.seeds.rng("population", "rates")
            run.save_array("rates_kg_h", rates)

    Parameters
    ----------
    config:
        The validated run config. Its ``seed`` roots the seed tree.
    root:
        Directory under which ``<run_id>/`` is created.
    run_id:
        Optional explicit id; default is ``<UTC timestamp>-<config hash prefix>``.
    """

    def __init__(self, config: RunConfig, root: str | Path = "runs", run_id: str | None = None) -> None:
        self.config = config
        self.seeds = SeedTree(config.seed)
        self.root = Path(root)
        cfg_hash = config_hash(config)
        stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_id = run_id or f"{stamp}-{cfg_hash[:8]}"
        self.dir = self.root / self.run_id
        self.dir.mkdir(parents=True, exist_ok=False)
        self._t0 = time.perf_counter()
        self._stage_t0: float | None = None
        self.manifest = RunManifest(
            run_id=self.run_id,
            name=config.name,
            seed=config.seed,
            config_sha256=cfg_hash,
            git=git_sha(),
            versions=_versions(),
            platform=f"{platform.system()} {platform.release()} {platform.machine()}",
            started_utc=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        )
        (self.dir / "config.yaml").write_text(canonical_yaml(config), encoding="utf-8")
        self._write_manifest()

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "RunContext":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.manifest.elapsed_s = round(time.perf_counter() - self._t0, 3)
        self.manifest.finished_utc = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        if exc is None:
            self.manifest.status = "ok"
        else:
            self.manifest.status = "error"
            self.manifest.error = f"{exc_type.__name__}: {exc}"
        self._write_manifest()

    def stage(self, name: str) -> "_Stage":
        """Time a pipeline stage: ``with run.stage("population"): ...``."""
        return _Stage(self, name)

    def _write_manifest(self) -> None:
        (self.dir / "manifest.json").write_text(self.manifest.to_json(), encoding="utf-8")

    # -- output writers ----------------------------------------------------
    def save_array(self, name: str, arr: np.ndarray) -> Path:
        """Write a numpy array as ``<name>.npy`` (byte-stable for a given array)."""
        path = self.dir / f"{name}.npy"
        np.save(path, np.ascontiguousarray(arr), allow_pickle=False)
        self.manifest.outputs[name] = path.name
        self._write_manifest()
        return path

    def save_json(self, name: str, doc: Mapping[str, Any] | list[Any]) -> Path:
        """Write a JSON document with sorted keys and fixed formatting."""
        path = self.dir / f"{name}.json"
        path.write_text(json.dumps(doc, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")
        self.manifest.outputs[name] = path.name
        self._write_manifest()
        return path

    def save_frame(self, name: str, frame: Any) -> Path:
        """Write a pandas DataFrame as CSV with a fixed float format and no index."""
        path = self.dir / f"{name}.csv"
        frame.to_csv(path, index=False, float_format="%.10g", lineterminator="\n")
        self.manifest.outputs[name] = path.name
        self._write_manifest()
        return path

    def output_files(self) -> dict[str, Path]:
        """Every deterministic output file (excludes ``manifest.json``)."""
        return {n: self.dir / f for n, f in self.manifest.outputs.items()} | {"config": self.dir / "config.yaml"}


class _Stage:
    def __init__(self, run: RunContext, name: str) -> None:
        self.run, self.name = run, name

    def __enter__(self) -> "_Stage":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc) -> None:  # type: ignore[no-untyped-def]
        self.run.manifest.stages[self.name] = round(time.perf_counter() - self._t0, 3)
        self.run._write_manifest()


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")


def compare_run_dirs(a: Path, b: Path) -> dict[str, bool]:
    """Byte-compare every deterministic output between two run directories.

    Returns ``{filename: identical}`` for the union of files in both, ignoring
    ``manifest.json`` (which carries wall-clock timestamps).
    """
    def _files(root: Path) -> set[str]:
        return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}

    names = _files(a) | _files(b)
    names.discard("manifest.json")
    result: dict[str, bool] = {}
    for n in sorted(names):
        pa, pb = a / n, b / n
        result[n] = pa.is_file() and pb.is_file() and pa.read_bytes() == pb.read_bytes()
    return result


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


__all__ = ["RunContext", "RunManifest", "compare_run_dirs", "git_sha"]
