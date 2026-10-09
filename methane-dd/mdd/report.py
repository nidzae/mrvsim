"""One HTML report per run (SPEC section 8a): delivered intensity, go/no-go, segment intensities, per-asset contributions, sources."""

from __future__ import annotations

import html
import json
from pathlib import Path

import numpy as np

from mdd.pipeline import RunResult

CSS = """body{font:14px/1.45 system-ui,sans-serif;margin:24px auto;max-width:1100px;color:#1d1d1b;padding:0 16px}
table{border-collapse:collapse;margin:8px 0 16px}td,th{border-bottom:1px solid #ddd;padding:4px 8px;text-align:left;font-size:13px}td.num{text-align:right}
.grade{display:inline-block;padding:4px 10px;border-radius:6px;color:#fff;font-weight:600}.Failed{background:#c0392b}.Flagged{background:#e67e22}.Screened{background:#7f8c8d}.Verified{background:#27ae60}
#map{height:420px;border:1px solid #ddd;border-radius:8px;margin:8px 0 16px}.legend{font-size:12px;color:#444;margin:-8px 0 16px}.legend span{display:inline-block;width:11px;height:11px;border-radius:50%;margin:0 4px -1px 10px}
.muted{color:#666}.tiles{display:flex;gap:12px;flex-wrap:wrap}.tile{border:1px solid #ddd;border-radius:8px;padding:10px 14px;min-width:180px}.tile b{font-size:20px}"""


def _pct(x: float, d: int = 2) -> str:
    return "—" if x is None or not np.isfinite(x) else f"{x * 100:.{d}f} %"


def _map_data(res: RunResult) -> dict:
    """GeoJSON layers for the report map: search areas, assets (sized by share of the assured intensity, coloured by
    whether a plume was attributed), attributed plumes, the customer site, and the scene footprints that were looked at."""
    import shapely
    pa = {r["asset_id"]: r for r in res.per_asset}
    share = {r["asset_id"]: r for r in res.scorecard.contributions}
    areas = [{"type": "Feature", "properties": {"name": f.name, "customer": f.customer}, "geometry": shapely.geometry.mapping(g)}
             for f, g in zip(res.area.features, res.area.buffered().geometry)]
    assets = []
    for a in res.assets:
        r = pa.get(a.asset_id, {}); c = share.get(a.asset_id, {})
        assets.append({"type": "Feature", "geometry": shapely.geometry.mapping(a.geometry),
                       "properties": {"name": a.name, "id": a.asset_id, "segment": a.segment, "operator": a.operator, "looks": int(r.get("n_looks", 0)),
                                      "detections": int(r.get("n_detections", 0)), "share": float(c.get("share_of_assured", 0.0)),
                                      "p95_t_yr": float(c.get("p95_kg_yr", 0.0)) / 1000.0, "throughput": a.throughput_source}})
    plumes = []
    if len(res.attributed):
        for _, r in res.attributed.drop_duplicates("plume_id").iterrows():
            plumes.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(r.lon), float(r.lat)]},
                           "properties": {"id": str(r.plume_id), "sensor": str(r.sensor), "when": str(r.timestamp)[:10],
                                          "rate": None if not np.isfinite(r.rate_kg_h) else float(r.rate_kg_h), "asset": str(r.asset_id), "prob": float(r.attribution_prob)}})
    customer = res.area.customer_site()
    cust = None if customer is None else {"type": "Feature", "properties": {"name": customer.name}, "geometry": shapely.geometry.mapping(customer.geometry.centroid)}
    scenes = []
    if len(res.looks) and len(res.scenes):
        seen = res.scenes[res.scenes.scene_id.isin(res.looks.scene_id.unique())]
        for _, r in seen.head(400).iterrows():
            scenes.append([[float(r.south), float(r.west)], [float(r.north), float(r.east)]])
    return {"areas": areas, "assets": assets, "plumes": plumes, "customer": cust, "scenes": scenes}


MAP_JS = """
const D = __DATA__;
const map = L.map('map');
// OpenStreetMap's and CARTO's tile servers refuse requests from a page opened as a local file; Esri's allow it.
const streets = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}', {maxZoom: 19, attribution: 'Map &copy; Esri'}).addTo(map);
const imagery = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {maxZoom: 19, attribution: 'Imagery &copy; Esri'});
const sceneLayer = L.layerGroup(D.scenes.map(b => L.rectangle(b, {color: '#3498db', weight: 0.5, fill: false, opacity: 0.25})));
const areaLayer = L.geoJSON({type: 'FeatureCollection', features: D.areas}, {style: f => ({color: f.properties.customer ? '#8e44ad' : '#2c3e50', weight: 1.5, fillOpacity: 0.05, dashArray: '4 3'}),
  onEachFeature: (f, l) => l.bindTooltip(f.properties.name)}).addTo(map);
const assetLayer = L.geoJSON({type: 'FeatureCollection', features: D.assets}, {
  pointToLayer: (f, ll) => L.circleMarker(ll, {radius: 4 + 18 * Math.sqrt(f.properties.share), color: '#fff', weight: 1, fillColor: f.properties.detections ? '#e67e22' : (f.properties.looks ? '#27ae60' : '#95a5a6'), fillOpacity: 0.85}),
  style: f => ({color: '#2c3e50', weight: 3}),
  onEachFeature: (f, l) => { const p = f.properties; l.bindPopup(`<b>${p.name}</b><br>${p.segment} · ${p.operator}<br>${p.looks} clear looks · ${p.detections} detections<br>p95 ${p.p95_t_yr.toFixed(1)} t/yr · ${(100 * p.share).toFixed(1)} % of assured<br><span style="color:#666">${p.throughput}</span>`); }}).addTo(map);
const plumeLayer = L.geoJSON({type: 'FeatureCollection', features: D.plumes}, {
  pointToLayer: (f, ll) => L.marker(ll, {icon: L.divIcon({className: '', html: '<div style="width:10px;height:10px;background:#c0392b;transform:rotate(45deg);border:1px solid #fff"></div>', iconSize: [10, 10]})}),
  onEachFeature: (f, l) => { const p = f.properties; l.bindPopup(`Plume ${p.id}<br>${p.sensor} · ${p.when}<br>${p.rate == null ? 'no rate published' : p.rate.toFixed(0) + ' kg/h'}<br>attributed to ${p.asset} (p = ${p.prob.toFixed(2)})`); }}).addTo(map);
if (D.customer) L.marker([D.customer.geometry.coordinates[1], D.customer.geometry.coordinates[0]], {icon: L.divIcon({className: '', html: '<div style="font-size:22px;line-height:22px">&#9733;</div>', iconSize: [22, 22]})}).bindTooltip('Customer: ' + D.customer.properties.name).addTo(map);
L.control.layers({'Streets': streets, 'Satellite': imagery}, {'Search areas': areaLayer, 'Assets': assetLayer, 'Attributed plumes': plumeLayer, 'Scene footprints (bounding boxes)': sceneLayer}, {collapsed: false}).addTo(map);
const b = areaLayer.getBounds(); if (b.isValid()) map.fitBounds(b.pad(0.15)); else map.setView([31.5, -103.5], 8);
"""


def write_report(res: RunResult, path: Path | None = None) -> Path:
    c = res.scorecard; m = res.meta
    path = path or res.run_dir / "report.html"
    g = c.grade.split(",")[0]
    rows_seg = "".join(f"<tr><td>{html.escape(s)}</td><td class=num>{v['n_assets']}</td><td class=num>{_pct(v['p5'])}</td><td class=num>{_pct(v['p50'])}</td>"
                       f"<td class=num>{_pct(v['assured'])}</td><td class=num>{_pct(v['measured_floor'])}</td><td class=num>{v['evidence_share'] * 100:.0f} %</td>"
                       f"<td><span class='grade {v['grade'].split(',')[0]}'>{html.escape(v['grade'])}</span></td></tr>" for s, v in c.segments.items())
    pa = {r["asset_id"]: r for r in res.per_asset}
    rows_assets = "".join(
        f"<tr><td>{html.escape(str(r.get('name', r['asset_id'])))}<div class=muted>{html.escape(r['asset_id'])}</div></td><td>{html.escape(r['segment'])}</td>"
        f"<td class=num>{pa.get(r['asset_id'], {}).get('n_looks', 0)}</td><td class=num>{pa.get(r['asset_id'], {}).get('n_detections', 0)}</td>"
        f"<td class=num>{r['p50_kg_yr'] / 1000:.1f}</td><td class=num>{r['p95_kg_yr'] / 1000:.1f}</td><td class=num>{r['floor_p5_kg_yr'] / 1000:.2f}</td>"
        f"<td class=num>{r['share_of_assured'] * 100:.1f} %</td><td class=num>{r['measured_share'] * 100:.0f} %</td><td>{html.escape(r['sub_basis'])}</td>"
        f"<td class=muted>{html.escape(str(pa.get(r['asset_id'], {}).get('throughput_source', '')))}</td></tr>" for r in c.contributions)
    u = c.units
    body = f"""<!doctype html><html><head><meta charset="utf-8"><title>Methane due diligence: {html.escape(res.run_dir.name)}</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"><script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>{CSS}</style></head><body>
<h1>Supply-chain methane due diligence</h1>
<p class=muted>Run {html.escape(res.run_dir.name)} · generated {html.escape(m['generated_utc'])} · window {html.escape(m['window'][0][:10])} to {html.escape(m['window'][1][:10])} · area {html.escape(res.area.source)}</p>
<div id=map></div>
<div class=legend>Where we looked: dashed outlines are the search areas (purple = customer site &#9733;), circles are assets sized by their share of the assured intensity
(<span style="background:#e67e22"></span>plume attributed <span style="background:#27ae60"></span>looked at, nothing seen <span style="background:#95a5a6"></span>no clear look),
red diamonds are attributed plumes, thin blue boxes are the scene footprints that counted as looks. Click anything for details.</div>
<p><span class="grade {g}">{html.escape(c.grade)}</span> &nbsp; <b>Recommendation:</b> {html.escape(c.recommendation)}</p>
<div class=tiles>
<div class=tile>Assured delivered intensity (p95)<br><b>{_pct(c.assured_intensity)}</b><div class=muted>{u['kg_ch4_per_mmbtu']:.3f} kg CH4/MMBtu · {u['g_co2e_per_mj']:.2f} g CO2e/MJ (GWP100 29.8)</div></div>
<div class=tile>Target<br><b>{_pct(c.target)}</b></div>
<div class=tile>Delivered intensity p5 / p50<br><b>{_pct(c.delivered['p5'])} / {_pct(c.delivered['p50'])}</b></div>
<div class=tile>Measured floor (p5 of detected sources)<br><b>{_pct(c.measured_floor)}</b><div class=muted>fails if above target</div></div>
<div class=tile>Evidence share<br><b>{c.evidence_share * 100:.0f} %</b><div class=muted>of the assured intensity that is measured</div></div>
<div class=tile>Data<br><b>{m['n_assets']} assets</b><div class=muted>{m['n_looks']} looks · {m['n_attributed']} attributed plume rows · {m['n_scenes']} scenes · {m['n_plumes']} plumes in the box</div></div>
</div>
<h2>Segments</h2>
<table><tr><th>segment</th><th>assets</th><th>p5</th><th>p50</th><th>assured (p95)</th><th>measured floor</th><th>evidence</th><th>grade</th></tr>{rows_seg}</table>
<h2>Assets, by contribution to the assured intensity</h2>
<table><tr><th>asset</th><th>segment</th><th>clear looks</th><th>detections</th><th>p50 t/yr</th><th>p95 t/yr</th><th>floor p5 t/yr</th><th>share of assured</th><th>measured</th><th>sub-threshold basis</th><th>throughput source</th></tr>{rows_assets}</table>
<h2>Method and limits</h2>
<ul>
<li>Duty factor with imperfect detection (SPEC 6a), prior: {html.escape(m['prior'])}; the per-asset table in <code>per_asset.parquet</code> also gives the uniform-prior median.</li>
<li>Sub-threshold emissions: {html.escape(m['sub_threshold'])}. A literature prior is an assumption, not a measurement; it caps the grade at "Screened, unverified".</li>
<li>Wind: {html.escape(m['wind'])}. Scene footprints are bounding boxes of Carbon Mapper scenes; exact polygons and cloud masks are a later step.</li>
<li>Customer shares: {html.escape('; '.join(m.get('share_notes', [])) or 'as supplied')}.</li>
<li>Throughputs: see the last column; every value names its source and period (SPEC 4b).</li>
</ul>
<h2>Sources</h2>
<ul><li>Carbon Mapper public API, plumes and scenes, retrieved {html.escape(', '.join(m['sources']['carbonmapper']['retrieved']) or 'from cache')} [carbonmapper-api]</li>
<li>OGIM v3.0 for assets and 2022 production [ogim]</li><li>Sensor detection curves: config/sensors.yaml with citations</li></ul>
<script>{MAP_JS.replace("__DATA__", json.dumps(_map_data(res)))}</script>
</body></html>"""
    path.write_text(body, encoding="utf-8")
    return path
