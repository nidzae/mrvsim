"""Carbon Mapper public API (SPEC section 2a, 5 stages 2-3): plumes and scene footprints [carbonmapper-api].

Both are fetched by bounding box and date window and cached as dated JSON so a score can be reproduced later.
Scenes give the non-detection record: every scene that covered an asset under clear sky is a look.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from shapely.geometry import Point, box

BASE = "https://api.carbonmapper.org/api/v1/catalog"
CACHE = Path(__file__).resolve().parents[2] / "cache" / "carbonmapper"
PAGE = 500


def _cached_get(path: str, params: list[tuple[str, Any]], cache_dir: Path = CACHE, refresh: bool = False) -> Any:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256((path + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:20]
    f = cache_dir / f"{key}.json"
    if f.exists() and not refresh:
        return json.loads(f.read_text(encoding="utf-8"))["body"]
    r = requests.get(f"{BASE}/{path}", params=params, timeout=120)
    r.raise_for_status()
    body = r.json()
    f.write_text(json.dumps({"retrieved_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "path": path, "params": params, "body": body}), encoding="utf-8")
    return body


def _paged(path: str, bbox: tuple[float, float, float, float], start: str | None, end: str | None, gas: str = "CH4", refresh: bool = False) -> list[dict]:
    items: list[dict] = []
    offset = 0
    while True:
        params: list[tuple[str, Any]] = [("limit", PAGE), ("offset", offset)] + [("bbox", v) for v in bbox]
        if gas and path.startswith("plumes"):
            params.append(("gas", gas))
        if start:
            params.append(("datetime_min" if path.startswith("plumes") else "timestamp_min", start))
        if end:
            params.append(("datetime_max" if path.startswith("plumes") else "timestamp_max", end))
        body = _cached_get(path, params, refresh=refresh)
        page = body.get("items", []) if isinstance(body, dict) else body
        items.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
    return items


def fetch_plumes(bbox: tuple[float, float, float, float], start: str | None = None, end: str | None = None, refresh: bool = False) -> pd.DataFrame:
    """Normalised plume table: source, sensor, timestamp (UTC), source location, emission rate, rate uncertainty."""
    rows = []
    for it in _paged("plumes/annotated", bbox, start, end, refresh=refresh):
        if it.get("gas") not in (None, "CH4"):
            continue
        lon, lat = it["geometry_json"]["coordinates"][:2]
        rows.append({"plume_id": it.get("plume_id"), "source": "carbonmapper", "sensor": it.get("instrument"), "platform": it.get("platform"),
                     "timestamp": pd.Timestamp(it["scene_timestamp"]), "scene_id": it.get("scene_id"), "lon": float(lon), "lat": float(lat),
                     "rate_kg_h": it.get("emission_auto"), "rate_unc_kg_h": it.get("emission_uncertainty_auto"), "wind_source": it.get("wind_source"),
                     "off_nadir": it.get("off_nadir"), "sensitivity_mode": it.get("sensitivity_mode"), "mission_phase": it.get("mission_phase")})
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["plume_id", "source", "sensor", "platform", "timestamp", "scene_id", "lon", "lat", "rate_kg_h", "rate_unc_kg_h", "wind_source"])
    return df.sort_values("timestamp").reset_index(drop=True)


def fetch_scenes(bbox: tuple[float, float, float, float], start: str | None = None, end: str | None = None, refresh: bool = False) -> pd.DataFrame:
    """Scene table: sensor, timestamp, footprint (bounds box; the exact polygon is in the downloadable GeoPackage), cloud cover."""
    rows = []
    for it in _paged("scenes/annotated", bbox, start, end, refresh=refresh):
        b = it.get("bounds")
        if not b:
            continue
        rows.append({"scene_id": it.get("id"), "name": it.get("name"), "sensor": it.get("instrument"), "platform": it.get("platform"),
                     "timestamp": pd.Timestamp(it["timestamp"]), "cloud_pct": it.get("cloud_cover_pct"), "cloud_assessed_pct": it.get("cloud_cover_pct_assessed"),
                     "not_cloudy": it.get("not_cloudy"), "solar_zenith_deg": it.get("solar_zenith_angle"), "off_nadir": it.get("off_nadir"),
                     "sensitivity_mode": it.get("sensitivity_mode"), "plume_count": it.get("published_plume_count"), "mission_phase": it.get("mission_phase"),
                     "west": b[0], "south": b[1], "east": b[2], "north": b[3], "footprint": box(*b)})
    df = pd.DataFrame(rows)
    return df.sort_values("timestamp").reset_index(drop=True) if not df.empty else df


def retrieval_dates(cache_dir: Path = CACHE) -> list[str]:
    out = set()
    for f in cache_dir.glob("*.json"):
        try:
            out.add(json.loads(f.read_text(encoding="utf-8"))["retrieved_utc"][:10])
        except Exception:  # noqa: BLE001
            pass
    return sorted(out)
