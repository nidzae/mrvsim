"""Asset discovery (SPEC section 4b): intersect the area with the OGIM infrastructure tables and propose assets.

Two sources, used in this order:
1. the OGIM v3.0 GeoPackage (``data/raw/ogim/OGIM_v3.0.gpkg`` in the MRVSim repo, git-ignored, 3.4 GB): production
   records with 2022 volumes, compressor stations, processing plants, storage fields [ogim];
2. the committed MRVSim site table (``data/fitted/sites.npz``, 577,007 production sites with production) and its
   midstream location pools, when the GeoPackage is absent.

Every proposed asset carries its throughput (methane, kg/yr) and where that number came from, because the
denominator moves the KPI as much as the numerator (SPEC 4b).
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from mdd.aoi import AreaOfInterest

MRVSIM_ROOT = Path(__file__).resolve().parents[2]
OGIM_GPKG = MRVSIM_ROOT / "data" / "raw" / "ogim" / "OGIM_v3.0.gpkg"
SITES_NPZ = MRVSIM_ROOT / "data" / "fitted" / "sites.npz"
M3_PER_MCF = 28.316847
CH4_KG_PER_M3 = 0.678 * 0.88          # methane density x default mole fraction in marketed gas (MRVSim constants)
# Carbon Mapper / OGIM segment vocabulary
MIDSTREAM_TABLES = {
    "gathering": ("Natural_Gas_Compressor_Stations", "FAC_TYPE = 'GATHERING COMPRESSOR STATION'"),
    "transmission": ("Natural_Gas_Compressor_Stations", "FAC_TYPE in ('TRANSMISSION COMPRESSOR STATION', 'BOOSTER PUMPING STATION, NATURAL GAS TRANSPORTATION')"),
    "processing": ("Gathering_and_Processing", None),
    "storage": ("Injection_and_Disposal", "FAC_TYPE in ('DEPLETED FIELD', 'AQUIFER', 'SALT DOME')"),
}


@dataclass
class Asset:
    asset_id: str
    name: str
    segment: str
    operator: str
    geometry: Any
    throughput_ch4_kg_yr: float | None
    throughput_source: str
    customer_share: float = 1.0
    confirmed: bool = False
    properties: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        return {"asset_id": self.asset_id, "name": self.name, "segment": self.segment, "operator": self.operator, "geometry": self.geometry,
                "throughput_ch4_kg_yr": self.throughput_ch4_kg_yr, "throughput_source": self.throughput_source, "customer_share": self.customer_share,
                "confirmed": self.confirmed, **{k: v for k, v in self.properties.items() if k != "geometry"}}


def assets_frame(assets: list[Asset]) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame([a.to_row() for a in assets], geometry="geometry", crs="EPSG:4326")


def _production_from_gpkg(con: sqlite3.Connection, bbox: tuple[float, float, float, float]) -> pd.DataFrame:
    w, s, e, n = bbox
    return pd.read_sql("select FAC_ID fac_id, FAC_NAME name, OPERATOR op, STATE_PROV state, ENTITY_TYPE entity, OIL_BBL oil, GAS_MCF gas, "
                       "CONDENSATE_BBL cond, LATITUDE lat, LONGITUDE lon from Oil_and_Natural_Gas_Production "
                       f"where COUNTRY = 'UNITED STATES OF AMERICA' and LONGITUDE between {w} and {e} and LATITUDE between {s} and {n}", con)


def discover(area: AreaOfInterest, include_segments: tuple[str, ...] = ("production", "gathering", "processing", "transmission", "storage"),
             gpkg: Path = OGIM_GPKG, sites_npz: Path = SITES_NPZ) -> list[Asset]:
    """Propose assets inside the area's search buffers."""
    search = area.union()
    bbox = search.bounds
    assets: list[Asset] = []
    if gpkg.exists():
        con = sqlite3.connect(f"file:{gpkg}?mode=ro", uri=True)
        if "production" in include_segments:
            df = _production_from_gpkg(con, bbox)
            df["gas"] = df.gas.clip(lower=0); df["oil"] = df.oil.clip(lower=0) + df.cond.clip(lower=0)
            df = df[(df.gas > 0) | (df.oil > 0)]
            pts = gpd.GeoSeries([Point(x, y) for x, y in zip(df.lon, df.lat)], crs="EPSG:4326")
            inside = pts.within(search).to_numpy()
            for (_, r), geom in zip(df[inside].iterrows(), pts[inside]):
                ch4 = float(r.gas) * M3_PER_MCF * CH4_KG_PER_M3
                assets.append(Asset(f"prod-{r.fac_id}", str(r["name"]) if r["name"] not in (None, "N/A") else f"{r.entity.title()} {r.fac_id}", "production",
                                    str(r.op), geom, ch4, f"OGIM v3.0 production record {r.fac_id}, 2022, {r.gas:.0f} Mcf gas [ogim]",
                                    properties={"state": r.state, "entity": r.entity, "gas_mcf_yr": float(r.gas), "oil_bbl_yr": float(r.oil), "source": "ogim-gpkg"}))
        for seg, (table, where) in MIDSTREAM_TABLES.items():
            if seg not in include_segments:
                continue
            w, s, e, n = bbox
            q = (f"select FAC_NAME name, OPERATOR op, FAC_TYPE typ, LATITUDE lat, LONGITUDE lon, GAS_THROUGHPUT_MMCFD thr, GAS_CAPACITY_MMCFD cap from {table} "
                 f"where COUNTRY = 'UNITED STATES OF AMERICA' and OGIM_STATUS != 'ABANDONED' and LONGITUDE between {w} and {e} and LATITUDE between {s} and {n}")
            if where:
                q += f" and {where}"
            try:
                df = pd.read_sql(q, con)
            except Exception:  # noqa: BLE001  tables without throughput columns
                df = pd.read_sql(q.replace(", GAS_THROUGHPUT_MMCFD thr, GAS_CAPACITY_MMCFD cap", ""), con); df["thr"] = np.nan; df["cap"] = np.nan
            for i, r in df.iterrows():
                geom = Point(r.lon, r.lat)
                if not geom.within(search):
                    continue
                thr = r.thr if pd.notna(r.thr) and r.thr > 0 else (r.cap if pd.notna(r.cap) and r.cap > 0 else np.nan)
                ch4 = float(thr) * 1000.0 * 365.0 * M3_PER_MCF * CH4_KG_PER_M3 if pd.notna(thr) else None
                src = (f"OGIM v3.0 {table} {'throughput' if pd.notna(r.thr) and r.thr > 0 else 'capacity'} {thr:.0f} MMcf/d [ogim]" if pd.notna(thr)
                       else "no throughput in OGIM; user must supply (SPEC 4b)")
                assets.append(Asset(f"{seg}-{i}", str(r["name"]) if r["name"] not in (None, "N/A") else f"{seg} {i}", seg, str(r.op), geom, ch4, src,
                                    properties={"fac_type": r.typ, "source": "ogim-gpkg"}))
    elif sites_npz.exists():
        with np.load(sites_npz) as z:
            lat, lon, gas, cell_idx, cell_keys = z["lat"], z["lon"], z["gas_mcf_yr"], z["cell_idx"], z["cell_keys"]
            pools = {k[5:]: z[k] for k in z.files if k.startswith("pool:")}
        w, s, e, n = bbox
        m = (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n)
        if "production" in include_segments:
            for i in np.nonzero(m)[0]:
                geom = Point(float(lon[i]), float(lat[i]))
                if geom.within(search):
                    assets.append(Asset(f"site-{i}", f"production site {i}", "production", "unknown (site table)", geom, float(gas[i]) * M3_PER_MCF * CH4_KG_PER_M3,
                                        f"MRVSim site table (OGIM 2022 production) row {i} [ogim]", properties={"cell": str(cell_keys[cell_idx[i]]), "source": "sites-npz"}))
        for key, arr in pools.items():
            seg = key.split("/")[1]
            if seg not in include_segments:
                continue
            for j, (la, lo) in enumerate(arr):
                geom = Point(float(lo), float(la))
                if w <= lo <= e and s <= la <= n and geom.within(search):
                    assets.append(Asset(f"{seg}-{j}", f"{seg} facility {j}", seg, "unknown (site table)", geom, None, "no throughput in the site table; user must supply", properties={"source": "sites-npz"}))
    return assets


def user_assets_from_area(area: AreaOfInterest) -> list[Asset]:
    """Area features that are themselves assets: pipeline routes (lines), and points or polygons whose label says ``asset``.

    Other points and polygons are search areas: the assets inside them come from discovery.
    """
    out = []
    for f in area.features:
        if f.customer:
            continue
        is_line = f.geometry.geom_type.endswith("LineString")
        if not is_line and "asset" not in (f.name + " " + str(f.properties)).lower():
            continue
        seg = f.segment_hint or ("transmission" if is_line else "production")
        out.append(Asset(f"user-{uuid.uuid4().hex[:8]}", f.name, seg, str(f.properties.get("operator", "unknown")), f.geometry, None,
                         "drawn by the user; throughput must be supplied", properties={"source": "area-file"}))
    return out
