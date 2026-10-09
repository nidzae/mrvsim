"""Area of interest (SPEC section 4a): KML/KMZ, GeoJSON or a coordinate list, with buffers and the customer site.

Points get a 500 m radius, lines a 250 m corridor each side, polygons are used as drawn. KML folder names are
read as segment hints. One feature flagged ``customer`` is the delivery point.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import unary_union

POINT_RADIUS_M = 500.0
LINE_CORRIDOR_M = 250.0
SEGMENTS = ("production", "gathering", "processing", "transmission", "storage", "distribution", "lng")
_KML_NS = "{http://www.opengis.net/kml/2.2}"


@dataclass
class AreaFeature:
    name: str
    geometry: Any                       # shapely, WGS84
    segment_hint: str | None = None
    customer: bool = False
    radius_m: float | None = None
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class AreaOfInterest:
    features: list[AreaFeature]
    source: str

    def frame(self) -> gpd.GeoDataFrame:
        rows = [{"name": f.name, "segment_hint": f.segment_hint, "customer": f.customer, "radius_m": f.radius_m, "geometry": f.geometry} for f in self.features]
        return gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")

    def buffered(self) -> gpd.GeoDataFrame:
        """Search area per feature: points buffered by their radius, lines by the corridor, polygons as drawn (metres via a local UTM)."""
        g = self.frame()
        utm = g.estimate_utm_crs()
        m = g.to_crs(utm)
        buf = []
        for f, geom in zip(self.features, m.geometry):
            if geom.geom_type == "Point":
                buf.append(geom.buffer(f.radius_m or POINT_RADIUS_M))
            elif geom.geom_type in ("LineString", "MultiLineString"):
                buf.append(geom.buffer(f.radius_m or LINE_CORRIDOR_M))
            else:
                buf.append(geom)
        m = m.set_geometry(gpd.GeoSeries(buf, crs=utm))
        return m.to_crs("EPSG:4326")

    def union(self):
        return unary_union(list(self.buffered().geometry))

    def bbox(self) -> tuple[float, float, float, float]:
        return tuple(float(x) for x in self.union().bounds)

    def customer_site(self) -> AreaFeature | None:
        return next((f for f in self.features if f.customer), None)


def _segment_hint(text: str | None) -> str | None:
    if not text:
        return None
    t = text.lower()
    for s in SEGMENTS:
        if s in t:
            return s
    if "well" in t or "pad" in t or "lease" in t:
        return "production"
    if "compressor" in t or "pipeline" in t:
        return "transmission"
    return None


def _parse_kml(text: str, source: str) -> AreaOfInterest:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(text)
    feats: list[AreaFeature] = []

    def walk(node, folder: str | None):
        for child in node:
            tag = child.tag.replace(_KML_NS, "")
            if tag in ("Folder", "Document"):
                nm = child.findtext(f"{_KML_NS}name") or folder
                walk(child, nm)
            elif tag == "Placemark":
                name = child.findtext(f"{_KML_NS}name") or f"feature {len(feats) + 1}"
                desc = (child.findtext(f"{_KML_NS}description") or "")
                geom = None
                for gtag, builder in (("Point", _kml_point), ("LineString", _kml_line), ("Polygon", _kml_polygon)):
                    el = child.find(f".//{_KML_NS}{gtag}")
                    if el is not None:
                        geom = builder(el); break
                if geom is None:
                    continue
                low = (name + " " + desc + " " + (folder or "")).lower()
                feats.append(AreaFeature(name, geom, _segment_hint(folder) or _segment_hint(name), "customer" in low, properties={"folder": folder, "description": desc}))

    walk(root, None)
    return AreaOfInterest(feats, source)


def _coords(el):
    txt = el.findtext(f".//{_KML_NS}coordinates") or ""
    pts = []
    for tok in txt.replace("\n", " ").split():
        parts = tok.split(",")
        if len(parts) >= 2:
            pts.append((float(parts[0]), float(parts[1])))
    return pts


def _kml_point(el):
    return Point(_coords(el)[0])


def _kml_line(el):
    return LineString(_coords(el))


def _kml_polygon(el):
    outer = el.find(f".//{_KML_NS}outerBoundaryIs")
    return Polygon(_coords(outer if outer is not None else el))


def _parse_geojson(obj: dict, source: str) -> AreaOfInterest:
    feats = []
    for i, f in enumerate(obj.get("features", [obj] if obj.get("type") == "Feature" else [])):
        props = f.get("properties") or {}
        name = str(props.get("name") or props.get("Name") or f"feature {i + 1}")
        geom = shape(f["geometry"])
        seg = props.get("segment") or _segment_hint(name) or _segment_hint(props.get("folder"))
        cust = bool(props.get("customer")) or "customer" in name.lower()
        feats.append(AreaFeature(name, geom, seg, cust, props.get("radius_m"), props))
    return AreaOfInterest(feats, source)


def _parse_coordinates(text: str, source: str) -> AreaOfInterest:
    """CSV or pasted lines: lat, lon[, label[, radius_m]]; a label containing 'customer' marks the delivery point."""
    feats = []
    reader = csv.reader(io.StringIO(text.strip()))
    for i, row in enumerate(reader):
        row = [c.strip() for c in row if c.strip() != ""]
        if not row:
            continue
        try:
            lat, lon = float(row[0]), float(row[1])
        except (ValueError, IndexError):
            if i == 0:
                continue                      # header
            raise
        label = row[2] if len(row) > 2 else f"site {i + 1}"
        radius = float(row[3]) if len(row) > 3 else None
        feats.append(AreaFeature(label, Point(lon, lat), _segment_hint(label), "customer" in label.lower(), radius))
    return AreaOfInterest(feats, source)


def load_area(path_or_text: str | Path) -> AreaOfInterest:
    """Load an area of interest from a .kml/.kmz/.geojson/.json/.csv file or from pasted coordinates."""
    p = Path(path_or_text) if isinstance(path_or_text, (str, Path)) and len(str(path_or_text)) < 400 and Path(str(path_or_text)).exists() else None
    if p is None:
        return _parse_coordinates(str(path_or_text), "pasted coordinates")
    suf = p.suffix.lower()
    if suf == ".kmz":
        with zipfile.ZipFile(p) as z:
            name = next(n for n in z.namelist() if n.lower().endswith(".kml"))
            return _parse_kml(z.read(name).decode("utf-8"), str(p))
    if suf == ".kml":
        return _parse_kml(p.read_text(encoding="utf-8"), str(p))
    if suf in (".geojson", ".json"):
        return _parse_geojson(json.loads(p.read_text(encoding="utf-8")), str(p))
    if suf in (".csv", ".txt"):
        return _parse_coordinates(p.read_text(encoding="utf-8"), str(p))
    raise ValueError(f"unsupported area file {p.suffix}")
