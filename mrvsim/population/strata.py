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

from mrvsim.population.sites import DEFAULT_SITES_PATH, SiteTable, load_sites

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
    # Real production sites behind the well-pad strata (TDD section 3.1 as amended 2026-10-03) [ogim]:
    # ``site_rows[h]`` are the rows of ``sites`` that make up stratum h (None for a stratum without site data).
    sites: SiteTable | None = None
    site_rows: tuple[np.ndarray | None, ...] = ()
    class_rule: str = "throughput"

    def rows_of(self, h: int) -> np.ndarray | None:
        return self.site_rows[h] if self.site_rows else None

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
        sizes = np.full(len(self.strata), int(n_per_stratum), dtype=np.int64)
        # A stratum of real sites cannot hold more facilities than it has sites [ogim]: it is then a census.
        for h, rows in enumerate(self.site_rows):
            if rows is not None:
                sizes[h] = min(sizes[h], rows.shape[0])
        return sizes


DEFAULT_WEIGHTS_PATH = Path(__file__).resolve().parents[2] / "data" / "fitted" / "strata_weights.json"


def load_strata(
    path: str | Path = DEFAULT_STRATA_PATH,
    n_classes: int | None = None,
    weights_path: str | Path | None = DEFAULT_WEIGHTS_PATH,
    sites: SiteTable | str | Path | None = DEFAULT_SITES_PATH,
    class_rule: str = "throughput",
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
    sites:
        Real production sites [ogim] (a :class:`SiteTable` or the path of ``data/fitted/sites.npz``). A cell
        with sites is split into throughput classes of its real sites, and each class's weights are its
        real share of the cell's site count and gas volume. ``None`` (or a missing file) keeps the even
        split across classes.
    class_rule:
        ``throughput`` (default since 2026-10-05): classes hold equal shares of the cell's produced energy, so the
        few large sites that carry most of the gas are sampled as heavily as the many small ones; every
        population statistic is then weighted (TDD section 3.1).
        ``count``: classes hold equal numbers of sites (the quantile classes of TDD section 3.1 as first written).
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
    # Each cell splits evenly across throughput classes (classes are quantiles by construction), unless the
    # cell has real sites: then each class carries its real share of the cell's sites and gas [ogim].
    wc = wc / wc.sum()
    wt = wt / wt.sum()
    if not isinstance(sites, SiteTable):
        sites = load_sites(sites)
    energy = None
    if sites is not None and len(sites):
        constants = yaml.safe_load((Path(__file__).resolve().parents[2] / "configs" / "constants.yaml").read_text(encoding="utf-8"))
        energy = sites.energy_mj_yr(float(constants["gas_hhv_mj_per_m3"]), float(constants["oil_mj_per_bbl"]))
    strata: list[Stratum] = []
    site_rows: list[np.ndarray | None] = []
    for ci, c in enumerate(cells):
        key = f"{c['basin']}/{c['facility_type']}"
        rows = sites.class_rows(key, len(classes), energy, class_rule) if energy is not None and key in sites.cell_keys else None
        if rows is not None and all(len(r) for r in rows):
            n_h = np.array([len(r) for r in rows], dtype=float)
            gas_h = np.array([sites.gas_m3_yr[r].sum() for r in rows])
            share_count = n_h / n_h.sum()
            share_thr = gas_h / gas_h.sum() if gas_h.sum() > 0 else share_count
        else:
            rows = None
            share_count = share_thr = np.full(len(classes), 1.0 / len(classes))
        for ki, cls in enumerate(classes):
            strata.append(
                Stratum(
                    index=len(strata),
                    basin=c["basin"],
                    facility_type=c["facility_type"],
                    throughput_class=cls,
                    throughput_class_index=ki,
                    n_classes=len(classes),
                    weight_count=float(wc[ci] * share_count[ki]),
                    weight_throughput=float(wt[ci] * share_thr[ki]),
                )
            )
            site_rows.append(rows[ki] if rows is not None else None)
    return StrataTable(
        provenance=provenance,
        basins=basins,
        facility_types=ftypes,
        throughput_classes=classes,
        strata=tuple(strata),
        sites=sites,
        site_rows=tuple(site_rows),
        class_rule=class_rule,
    )


LEGACY_STRATA_PATH = Path(__file__).resolve().parents[2] / "configs" / "strata_legacy_2026-09-30.yaml"


def load_saved_strata(population_dir: str | Path) -> StrataTable:
    """The strata table a saved population was generated with.

    Reads ``strata.json`` written by ``Population.save``. Populations saved before 2026-10-03 have none
    and used the 63-stratum layout in ``configs/strata_legacy_2026-09-30.yaml`` (placeholder weights).
    """
    path = Path(population_dir) / "strata.json"
    if not path.exists():
        return load_strata(LEGACY_STRATA_PATH, weights_path=None, sites=None)
    saved = json.loads(path.read_text(encoding="utf-8"))
    base = load_strata(weights_path=None, sites=None)
    strata = tuple(Stratum(index=i, basin=t["basin"], facility_type=t["facility_type"], throughput_class=t["throughput_class"],
                           throughput_class_index=int(t["throughput_class_index"]), n_classes=int(t["n_classes"]),
                           weight_count=float(t["weight_count"]), weight_throughput=float(t["weight_throughput"]))
                   for i, t in enumerate(saved["strata"]))
    return StrataTable(provenance=str(saved["provenance"]), basins=base.basins, facility_types=base.facility_types,
                       throughput_classes=tuple(saved["throughput_classes"]), strata=strata, class_rule=str(saved.get("class_rule", "count")))
