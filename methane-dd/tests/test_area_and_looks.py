"""Area loading, discovery fallbacks, looks and attribution on synthetic inputs (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from mdd.aoi import load_area
from mdd.discover import Asset
from mdd.looks import attribute_plumes, build_looks, dedupe
from mdd.sensors import load_sensors


def test_area_formats(tmp_path: Path) -> None:
    csv = tmp_path / "a.csv"; csv.write_text("lat,lon,label,radius_m\n32.2,-103.7,pads,1000\n31.9,-103.6,customer meter,300\n")
    a = load_area(csv)
    assert len(a.features) == 2 and a.customer_site().name == "customer meter" and a.features[0].radius_m == 1000
    w, s, e, n = a.bbox(); assert w < -103.7 < e and s < 32.2 < n
    gj = tmp_path / "a.geojson"
    gj.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": "gathering line"}, "geometry": {"type": "LineString", "coordinates": [[-103.7, 32.2], [-103.6, 32.3]]}},
        {"type": "Feature", "properties": {"name": "field", "customer": False}, "geometry": {"type": "Polygon", "coordinates": [[[-103.8, 32.1], [-103.6, 32.1], [-103.6, 32.3], [-103.8, 32.3], [-103.8, 32.1]]]}}]}))
    g = load_area(gj); assert g.features[0].segment_hint == "gathering" and g.features[1].geometry.geom_type == "Polygon"
    kml = tmp_path / "a.kml"
    kml.write_text('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder><name>Processing</name>'
                   '<Placemark><name>plant A</name><Point><coordinates>-103.65,32.25,0</coordinates></Point></Placemark></Folder>'
                   '<Placemark><name>Customer site</name><Point><coordinates>-103.6,31.9,0</coordinates></Point></Placemark></Document></kml>')
    k = load_area(kml); assert k.features[0].segment_hint == "processing" and k.features[1].customer
    pasted = load_area("32.2, -103.7, pads\n31.9, -103.6, customer")
    assert len(pasted.features) == 2 and pasted.customer_site() is not None


def _assets():
    return [Asset("A", "pad A", "production", "op", Point(-103.70, 32.20), 1e5, "test"),
            Asset("B", "pad B", "production", "op", Point(-103.70, 32.203), 1e5, "test"),      # 330 m north of A
            Asset("C", "pad C", "production", "op", Point(-103.60, 32.30), 1e5, "test")]


def test_attribution_splits_between_close_neighbours() -> None:
    plumes = pd.DataFrame([{"plume_id": "p1", "source": "t", "sensor": "tan", "platform": "Tanager", "timestamp": pd.Timestamp("2025-01-01T18:00Z"), "scene_id": "s1",
                            "lon": -103.70, "lat": 32.2005, "rate_kg_h": 300.0, "rate_unc_kg_h": 100.0, "wind_source": None}])
    at = attribute_plumes(_assets(), plumes, None, None)
    assert set(at.asset_id) == {"A", "B"} and at.attribution_prob.sum() == pytest.approx(1.0)
    assert at.set_index("asset_id").attribution_prob["A"] > at.set_index("asset_id").attribution_prob["B"]   # nearer asset gets more


def test_dedupe_merges_two_sensors_same_event() -> None:
    t = pd.Timestamp("2025-01-01T18:00Z")
    pl = pd.DataFrame([{"plume_id": "p1", "sensor": "tan", "timestamp": t, "lon": -103.70, "lat": 32.20, "rate_kg_h": 300.0},
                       {"plume_id": "p2", "sensor": "emi", "timestamp": t + pd.Timedelta(minutes=20), "lon": -103.7005, "lat": 32.2005, "rate_kg_h": 400.0},
                       {"plume_id": "p3", "sensor": "tan", "timestamp": t + pd.Timedelta(days=3), "lon": -103.70, "lat": 32.20, "rate_kg_h": 350.0}])
    d = dedupe(pl)
    assert list(d.plume_id) == ["p1", "p3"] and d.other_rate_kg_h.iloc[0] == 400.0


def test_looks_count_clear_scenes_only() -> None:
    lib = load_sensors()
    scenes = pd.DataFrame([{"scene_id": f"s{i}", "name": f"s{i}", "sensor": "tan", "platform": "Tanager", "timestamp": pd.Timestamp("2025-01-01T18:00Z") + pd.Timedelta(days=i),
                            "cloud_pct": 80.0 if i == 2 else 5.0, "cloud_assessed_pct": None, "not_cloudy": i != 2, "solar_zenith_deg": 30.0, "off_nadir": 10.0,
                            "sensitivity_mode": "standard", "plume_count": 0, "mission_phase": "production", "west": -103.8, "south": 32.1, "east": -103.65, "north": 32.25,
                            "footprint": box(-103.8, 32.1, -103.65, 32.25)} for i in range(4)])
    plumes = pd.DataFrame([{"plume_id": "p1", "source": "t", "sensor": "tan", "platform": "Tanager", "timestamp": scenes.timestamp.iloc[1], "scene_id": "s1",
                            "lon": -103.70, "lat": 32.20, "rate_kg_h": 300.0, "rate_unc_kg_h": 100.0, "wind_source": None}])
    looks, at = build_looks(_assets(), scenes, plumes, lib)
    a = looks[looks.asset_id == "A"]
    assert len(a) == 4 and a.clear.sum() == 3 and a.detected.sum() == 1 and (looks.asset_id == "C").sum() == 0   # C lies outside the footprint
