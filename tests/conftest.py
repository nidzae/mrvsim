"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from mrvsim.io.config import RunConfig

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def small_config() -> RunConfig:
    """A tiny config for fast tests."""
    return RunConfig.from_dict(
        {"name": "test", "seed": 12345, "year": 2024, "replications": 2, "population": {"n_per_stratum": 30}}
    )
