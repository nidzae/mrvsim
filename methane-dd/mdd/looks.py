"""Look table and attribution (SPEC section 5, stages 3, 5 and 6).

A **look** is one scene that covered an asset under clear enough sky: (asset, sensor, timestamp, cloud, wind,
detected). Detections are plumes attributed to the asset in that scene. Attribution uses the plume's source
location (Carbon Mapper gives one) and the distance to every candidate asset, including OGIM neighbours that are
not on the contract path, so that a neighbour's plume is not charged to the asset: the attribution probability
of asset a is exp(-d_a / s) over the sum across candidates within the search radius.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

from mdd.discover import Asset
from mdd.sensors import SensorLibrary

ATTRIBUTION_RADIUS_M = 500.0
ATTRIBUTION_SCALE_M = 150.0
DEDUP_SECONDS = 3600.0
DEDUP_METRES = 200.0


def _utm(frame: gpd.GeoDataFrame):
    return frame.estimate_utm_crs()


def build_looks(assets: list[Asset], scenes: pd.DataFrame, plumes: pd.DataFrame, lib: SensorLibrary, start: pd.Timestamp | None = None,
                end: pd.Timestamp | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (looks, attributed plumes).

    looks: one row per (asset, scene) where the scene footprint contains the asset and cloud cover is below the
    clear-sky threshold (scenes without a cloud figure are kept and flagged). Sensors without a model are skipped.
    """
    if scenes.empty or not assets:
        return pd.DataFrame(), pd.DataFrame()
    sc = scenes.copy()
    if start is not None:
        sc = sc[sc.timestamp >= start]
    if end is not None:
        sc = sc[sc.timestamp <= end]
    sc = sc[sc.sensor.isin(list(lib.sensors))]
    pl = attribute_plumes(assets, plumes, start, end)
    rows = []
    for a in assets:
        pt = a.geometry if a.geometry.geom_type == "Point" else a.geometry.representative_point()
        cover = sc[(sc.west <= pt.x) & (sc.east >= pt.x) & (sc.south <= pt.y) & (sc.north >= pt.y)]
        det_scenes = set(pl[pl.asset_id == a.asset_id].scene_id) if not pl.empty else set()
        for _, s in cover.iterrows():
            cloud = s.cloud_pct if pd.notna(s.cloud_pct) else (s.cloud_assessed_pct if pd.notna(s.cloud_assessed_pct) else np.nan)
            clear = bool(cloud <= lib.clear_sky_max_cloud_pct) if pd.notna(cloud) else True
            rows.append({"asset_id": a.asset_id, "scene_id": s.scene_id, "sensor": s.sensor, "timestamp": s.timestamp, "cloud_pct": cloud, "cloud_known": pd.notna(cloud),
                         "clear": clear, "wind_m_s": lib.default_wind_m_s, "wind_source": "default (no reanalysis attached)",
                         "detected": s.scene_id in det_scenes, "footprint": "scene bounding box (SPEC: exact polygon from the GeoPackage is a later step)"})
    looks = pd.DataFrame(rows)
    return looks, pl


def attribute_plumes(assets: list[Asset], plumes: pd.DataFrame, start: pd.Timestamp | None, end: pd.Timestamp | None) -> pd.DataFrame:
    """Plumes within the attribution radius of any asset, with an attribution probability per (plume, asset)."""
    if plumes.empty or not assets:
        return pd.DataFrame(columns=["plume_id", "asset_id", "attribution_prob"])
    pl = plumes.copy()
    if start is not None:
        pl = pl[pl.timestamp >= start]
    if end is not None:
        pl = pl[pl.timestamp <= end]
    pl = dedupe(pl)
    af = gpd.GeoDataFrame({"asset_id": [a.asset_id for a in assets]}, geometry=[a.geometry if a.geometry.geom_type == "Point" else a.geometry.representative_point() for a in assets], crs="EPSG:4326")
    utm = _utm(af)
    am = af.to_crs(utm)
    pm = gpd.GeoDataFrame(pl, geometry=[Point(x, y) for x, y in zip(pl.lon, pl.lat)], crs="EPSG:4326").to_crs(utm)
    ax, ay = am.geometry.x.to_numpy(), am.geometry.y.to_numpy()
    rows = []
    for _, p in pm.iterrows():
        d = np.hypot(ax - p.geometry.x, ay - p.geometry.y)
        near = np.nonzero(d <= ATTRIBUTION_RADIUS_M)[0]
        if near.size == 0:
            continue
        w = np.exp(-d[near] / ATTRIBUTION_SCALE_M); w = w / w.sum()
        for k, i in enumerate(near):
            rows.append({**{c: p[c] for c in pl.columns}, "asset_id": af.asset_id.iloc[i], "distance_m": float(d[i]), "attribution_prob": float(w[k]), "n_candidates": int(near.size)})
    return pd.DataFrame(rows)


def dedupe(pl: pd.DataFrame) -> pd.DataFrame:
    """Merge detections of the same event by two sensors within an hour and 200 m; keep the first row, note the other rate."""
    if pl.empty:
        return pl
    pl = pl.sort_values("timestamp").reset_index(drop=True).copy()
    pl["duplicate_of"] = None; pl["other_rate_kg_h"] = np.nan
    t = pl.timestamp.astype("int64") / 1e9
    lat, lon = pl.lat.to_numpy(), pl.lon.to_numpy()
    keep = np.ones(len(pl), dtype=bool)
    for i in range(len(pl)):
        if not keep[i]:
            continue
        close = (np.abs(t - t[i]) <= DEDUP_SECONDS) & (np.arange(len(pl)) > i)
        if not close.any():
            continue
        dm = np.hypot((lat - lat[i]) * 111_000.0, (lon - lon[i]) * 111_000.0 * np.cos(np.deg2rad(lat[i])))
        dup = close & (dm <= DEDUP_METRES) & (pl.sensor.to_numpy() != pl.sensor.iloc[i])
        for j in np.nonzero(dup)[0]:
            keep[j] = False; pl.loc[j, "duplicate_of"] = pl.plume_id.iloc[i]; pl.loc[i, "other_rate_kg_h"] = pl.rate_kg_h.iloc[j]
    return pl[keep].reset_index(drop=True)
