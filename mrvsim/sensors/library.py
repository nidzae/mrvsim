"""Sensor library: every YAML in ``configs/sensors`` (TDD section 4)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from mrvsim.sensors.schema import Sensor, load_sensor

_REPO = Path(__file__).resolve().parents[2]
DEFAULT_SENSOR_DIR = _REPO / "configs" / "sensors"
REFERENCES_PATH = _REPO / "docs" / "REFERENCES.md"


@dataclass(frozen=True)
class SensorLibrary:
    sensors: dict[str, Sensor]

    def __iter__(self) -> Iterator[Sensor]:
        return iter(self.sensors.values())

    def __len__(self) -> int:
        return len(self.sensors)

    def __getitem__(self, key: str) -> Sensor:
        return self.sensors[key]

    def by_class(self, sensor_class: str) -> list[Sensor]:
        return [s for s in self if s.sensor_class == sensor_class]

    def certifiable(self, min_tier: str = "A") -> list[Sensor]:
        """Sensors allowed to contribute to certification (PRD N2): tier <= min_tier and enabled."""
        order = "ABCD"
        return [s for s in self if s.enabled_for_certification and order.index(s.tier) <= order.index(min_tier)]

    def citation_keys(self, keys: list[str] | None = None) -> tuple[str, ...]:
        sel = self.sensors.values() if keys is None else [self.sensors[k] for k in keys]
        out: set[str] = set()
        for s in sel:
            out |= set(s.citation_keys())
        return tuple(sorted(out))


def load_library(directory: str | Path = DEFAULT_SENSOR_DIR) -> SensorLibrary:
    d = Path(directory)
    sensors: dict[str, Sensor] = {}
    for p in sorted(d.glob("*.yaml")):
        if p.name.startswith("_"):
            continue
        s = load_sensor(p)
        if s.key in sensors:
            raise ValueError(f"duplicate sensor key {s.key!r} in {p}")
        sensors[s.key] = s
    if not sensors:
        raise ValueError(f"no sensor YAML files in {d}")
    return SensorLibrary(sensors)


def reference_keys(path: str | Path = REFERENCES_PATH) -> dict[str, str]:
    """Citation key -> status, parsed from the REFERENCES.md table (for attribution and N1 checks)."""
    out: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*([a-z0-9\-]+)\s*\|.*\|\s*([^|]+?)\s*\|\s*$", line)
        if m and m.group(1) not in ("key",):
            out[m.group(1)] = m.group(2).strip()
    return out


def unresolved_citations(lib: SensorLibrary, refs: dict[str, str] | None = None) -> dict[str, list[str]]:
    """Sensor key -> citation keys that do not resolve in REFERENCES.md."""
    refs = refs or reference_keys()
    bad: dict[str, list[str]] = {}
    for s in lib:
        missing = [k for k in s.citation_keys() if k not in refs]
        if missing:
            bad[s.key] = missing
    return bad
