"""Loader for ``data/fitted/validation_targets.yaml`` (TDD section 9)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

DEFAULT_TARGETS_PATH = Path(__file__).resolve().parents[2] / "data" / "fitted" / "validation_targets.yaml"


def load_targets(path: str | Path = DEFAULT_TARGETS_PATH) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def target_missing(block: Mapping[str, Any]) -> bool:
    """A target block is missing if its status says so or its value is null."""
    if block.get("status") == "missing":
        return True
    v = block.get("value", "n/a")
    return v is None


def worst_status(blocks: list[Mapping[str, Any]]) -> str:
    order = {"ok": 0, "verify": 1, "missing": 2}
    return max((str(b.get("status", "missing")) for b in blocks), key=lambda s: order.get(s, 2), default="missing")
