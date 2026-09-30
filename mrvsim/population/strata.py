"""Stratification (TDD section 3.1): basin x facility type x throughput class.

Loads ``configs/strata.yaml`` into a :class:`StrataTable` with one row per
stratum and normalised weights for facility-count and throughput aggregates.
Weights are PLACEHOLDER until ``data/fitted/strata_weights.json`` exists
[ghgrp; state-production-data].
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import yaml

DEFAULT_STRATA_PATH = Path(__file__).resolve().parents[2] / "configs" / "strata.yaml"


@dataclass(frozen=True)
class Basin:
    key: str
    name: str
    lat_range: tuple[float, float]
    lon_range: tuple[float, float]


@dataclass(frozen=True)
class FacilityType:
    key: str
    name: str
    ghgrp_reporter_share: float


@dataclass(frozen=True)
class Stratum:
    index: int
    basin: str
    facility_type: str
    throughput_class: str
    throughput_class_index: int      # 0..n_classes-1
    n_classes: int
    weight_count: float              # W_h for facility-count aggregates (sums to 1)
    weight_throughput: float         # W_h for throughput aggregates (sums to 1)

    @property
    def key(self) -> str:
        return f"{self.basin}/{self.facility_type}/{self.throughput_class}"


@dataclass(frozen=True)
class StrataTable:
    provenance: str
    basins: Mapping[str, Basin]
    facility_types: Mapping[str, FacilityType]
    throughput_classes: tuple[str, ...]
    strata: tuple[Stratum, ...]

    @property
    def is_placeholder(self) -> bool:
        return self.provenance.upper() == "PLACEHOLDER"

    def __len__(self) -> int:
        return len(self.strata)

    def basin_index(self) -> dict[str, int]:
        return {k: i for i, k in enumerate(self.basins)}

    def facility_type_index(self) -> dict[str, int]:
        return {k: i for i, k in enumerate(self.facility_types)}

    def weights_count(self) -> np.ndarray:
        return np.array([s.weight_count for s in self.strata])

    def weights_throughput(self) -> np.ndarray:
        return np.array([s.weight_throughput for s in self.strata])

    def sizes(self, n_per_stratum: int, min_per_stratum: int = 30) -> np.ndarray:
        """Facilities simulated per stratum, n_h (TDD section 3.1: default 100, minimum 30)."""
        if n_per_stratum < min_per_stratum:
            raise ValueError(f"n_per_stratum={n_per_stratum} below minimum {min_per_stratum} (TDD section 3.1)")
        return np.full(len(self.strata), int(n_per_stratum), dtype=np.int64)


DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "data" / "fitted" / "strata_weights.json"


def load_strata(
    path: str | Path = DEFAULT_STRATA_PATH,
    n_classes: int | None = None,
    weights_path: str | Path | None = DEFAULT_WEIGHTS_PATH,
) -> StrataTable:
    """Load the strata table.

    Parameters
    ----------
    n_classes:
        Override the number of throughput classes (3 = terciles by default;
        5 = quintiles for the V4 stability test). Class labels become ``t1..tN``.
    weights_path:
        Fitted cell weights (``data/fitted/strata_weights.json`` from
        ``data/scripts/fit_strata_weights.py`` [ghgrp]). If the file exists its
        ``weight_count`` / ``weight_throughput`` replace the YAML placeholders
        and the table's provenance becomes the file's. Pass ``None`` to force
        the YAML values.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    basins = {
        k: Basin(k, v["name"], (float(v["lat"][0]), float(v["lat"][1])), (float(v["lon"][0]), float(v["lon"][1])))
        for k, v in raw["basins"].items()
    }
    ftypes = {k: FacilityType(k, v["name"], float(v.get("ghgrp_reporter_share", 0.0))) for k, v in raw["facility_types"].items()}
    classes = tuple(raw["throughput_classes"]) if n_classes is None else tuple(f"t{i+1}" for i in range(n_classes))
    cells: list[Mapping[str, Any]] = raw["cells"]
    for c in cells:
        if c["basin"] not in basins:
            raise ValueError(f"unknown basin {c['basin']!r} in strata cells")
        if c["facility_type"] not in ftypes:
            raise ValueError(f"unknown facility_type {c['facility_type']!r} in strata cells")
    provenance = str(raw.get("provenance", "UNKNOWN"))
    wc = np.array([float(c["weight_count"]) for c in cells])
    wt = np.array([float(c["weight_throughput"]) for c in cells])
    if weights_path is not None and Path(weights_path).exists():
        fitted = json.loads(Path(weights_path).read_text(encoding="utf-8"))
        by_cell = {(c["basin"], c["facility_type"]): c for c in fitted["cells"]}
        missing = [(c["basin"], c["facility_type"]) for c in cells if (c["basin"], c["facility_type"]) not in by_cell]
        if missing:
            raise ValueError(f"{weights_path} lacks weights for cells {missing}; re-run fit_strata_weights.py")
        wc = np.array([float(by_cell[(c["basin"], c["facility_type"])]["weight_count"]) for c in cells])
        wt = np.array([float(by_cell[(c["basin"], c["facility_type"])]["weight_throughput"]) for c in cells])
        provenance = f"{fitted.get('provenance', 'FITTED')} ({Path(weights_path).name}, GHGRP RY{fitted.get('reporting_year')})"
    # Each cell splits evenly across throughput classes (classes are quantiles by construction).
    wc = wc / wc.sum() / len(classes)
    wt = wt / wt.sum() / len(classes)
    strata: list[Stratum] = []
    for ci, c in enumerate(cells):
        for ki, cls in enumerate(classes):
            strata.append(
                Stratum(
                    index=len(strata),
                    basin=c["basin"],
                    facility_type=c["facility_type"],
                    throughput_class=cls,
                    throughput_class_index=ki,
                    n_classes=len(classes),
                    weight_count=float(wc[ci]),
                    weight_throughput=float(wt[ci]),
                )
            )
    return StrataTable(
        provenance=provenance,
        basins=basins,
        facility_types=ftypes,
        throughput_classes=classes,
        strata=tuple(strata),
    )
