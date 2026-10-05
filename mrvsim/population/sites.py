"""Real production sites (TDD section 3.1 as amended 2026-10-03) [ogim].

``data/fitted/sites.npz`` (written by ``data/scripts/build_sites.py``) lists every onshore US
production site with reported production: location, gas and oil volumes, and its basin x type cell.
The population generator samples its well-pad facilities from this table, so a sampled facility
has a real location and a real production volume; its emissions remain synthetic. Midstream cells
have location pools only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import numpy as np

DEFAULT_SITES_PATH = Path(__file__).resolve().parents[2] / "data" / "fitted" / "sites.npz"
M3_PER_MCF = 28.316847          # 1,000 cubic feet in cubic metres (exact unit conversion)
CLASS_RULES = ("count", "throughput")


@dataclass(frozen=True)
class SiteTable:
    cell_keys: tuple[str, ...]              # "<basin>/<facility_type>"
    cell_idx: np.ndarray                    # (n_sites,) index into cell_keys
    lat: np.ndarray
    lon: np.ndarray
    gas_m3_yr: np.ndarray                   # reported produced gas, used as marketed gas V_gas,mkt [ogim]
    oil_bbl_yr: np.ndarray
    n_wells: np.ndarray                     # wells behind the site (estimated for lease-level records) [ogim]
    pools: Mapping[str, np.ndarray] = field(default_factory=dict)   # midstream "<basin>/<type>" -> (N, 2) lat, lon
    source_path: str = ""

    def __len__(self) -> int:
        return int(self.cell_idx.shape[0])

    def energy_mj_yr(self, gas_hhv_mj_per_m3: float, oil_mj_per_bbl: float) -> np.ndarray:
        return self.gas_m3_yr * gas_hhv_mj_per_m3 + self.oil_bbl_yr * oil_mj_per_bbl

    def class_rows(self, cell_key: str, n_classes: int, energy_mj_yr: np.ndarray, rule: str = "throughput") -> list[np.ndarray]:
        """Site rows of ``cell_key`` split into ``n_classes`` throughput classes, smallest first.

        Sites are ranked by produced energy (gas plus oil). ``count`` gives classes with equal numbers
        of sites (the TDD section 3.1 quantile classes); ``throughput`` gives classes holding equal
        shares of the cell's energy, so the few large sites that carry most of the gas get their own class.
        """
        if rule not in CLASS_RULES:
            raise ValueError(f"throughput_class_rule must be one of {CLASS_RULES}, got {rule!r}")
        rows = np.nonzero(self.cell_idx == self.cell_keys.index(cell_key))[0]
        rows = rows[np.argsort(energy_mj_yr[rows], kind="stable")]
        if rule == "count":
            cls = (np.arange(rows.shape[0]) * n_classes) // max(rows.shape[0], 1)
        else:
            e = energy_mj_yr[rows]
            before = np.concatenate([[0.0], np.cumsum(e)[:-1]])
            cls = np.minimum((before / e.sum() * n_classes).astype(np.int64), n_classes - 1)
        return [rows[cls == k] for k in range(n_classes)]


def load_sites(path: str | Path | None = DEFAULT_SITES_PATH) -> SiteTable | None:
    """Load the site table, or None when ``path`` is None or the file does not exist."""
    if path is None or not Path(path).exists():
        return None
    with np.load(Path(path)) as z:
        pools = {k[len("pool:"):]: z[k] for k in z.files if k.startswith("pool:")}
        if "cell_idx" not in z.files:           # a pools-only file
            e = np.array([], dtype=np.float64)
            return SiteTable((), np.array([], dtype=np.int64), e, e, e, e, np.array([], dtype=np.int64), pools, str(path))
        return SiteTable(
            cell_keys=tuple(str(k) for k in z["cell_keys"]), cell_idx=z["cell_idx"].astype(np.int64),
            lat=z["lat"].astype(np.float64), lon=z["lon"].astype(np.float64),
            gas_m3_yr=z["gas_mcf_yr"].astype(np.float64) * M3_PER_MCF, oil_bbl_yr=z["oil_bbl_yr"].astype(np.float64),
            n_wells=z["n_wells"].astype(np.int64), pools=pools, source_path=str(path),
        )
