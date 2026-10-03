import React, { useEffect, useMemo, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import { api, classify, precision, halfWidth, fmtNum, fmtPct } from "../api.js";
import Drilldown from "./Drilldown.jsx";

const COLORS = { certified: "#0ca30c", fails: "#d03b3b", indeterminate: "#8d8b84", unscored: "#555" };
const STYLE = {
  version: 8,
  sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap contributors" } },
  layers: [{ id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.7, "raster-brightness-max": 0.9 } }],
};

// Facility map with three-state colouring at the user's (B, w_max) (PRD F9). Well-pad dots sit on real production sites
// [ogim] with synthetic emissions; the grey background layer is the density of all real sites the sample stands for.
export default function MapView({ runId, kpi, bar, barIntensity, barMass, wMax }) {
  const ref = useRef(null); const mapRef = useRef(null);
  const [geo, setGeo] = useState(null); const [sel, setSel] = useState(null); const [err, setErr] = useState(null);
  const [density, setDensity] = useState(null); const [showDensity, setShowDensity] = useState(true); const [ready, setReady] = useState(false);

  useEffect(() => { api.siteDensity().then((d) => setDensity({ n: d.n_sites, geo: { type: "FeatureCollection",
    features: (d.cells || []).map(([lon, lat, n]) => ({ type: "Feature", geometry: { type: "Point", coordinates: [lon, lat] }, properties: { n } })) } })).catch(() => setDensity(null)); }, []);

  useEffect(() => { if (!runId) return; setGeo(null); api.facilities(runId, kpi).then(setGeo).catch((e) => setErr(String(e))); }, [runId, kpi]);

  const colored = useMemo(() => {
    if (!geo) return null;
    const B = kpi === "intensity" ? bar : bar * 1000; // mass bar in t/yr -> kg/yr
    return { ...geo, features: geo.features.map((f) => ({ ...f, properties: { ...f.properties, state: classify(f.properties, B), precision: precision(f.properties, wMax), prior_only: f.properties.prior_only === true } })) };
  }, [geo, bar, wMax, kpi]);

  const counts = useMemo(() => {
    const c = { certified: 0, fails: 0, indeterminate: 0, wide: 0, prior_only: 0 };
    colored?.features.forEach((f) => { const q = f.properties; if (c[q.state] !== undefined) c[q.state]++; if (q.precision === "wide") c.wide++; if (q.state === "certified" && q.prior_only) c.prior_only++; });
    return c;
  }, [colored]);

  useEffect(() => {
    if (mapRef.current || !ref.current) return;
    const map = new maplibregl.Map({ container: ref.current, style: STYLE, center: [-98, 37], zoom: 3.6, attributionControl: true });
    map.addControl(new maplibregl.NavigationControl(), "top-left");
    map.on("load", () => {
      // background: all real production sites, counted per 0.1 degree cell (drawn under the sampled facilities)
      map.addSource("sites", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "sites", type: "circle", source: "sites", paint: {
        "circle-radius": ["interpolate", ["exponential", 2], ["zoom"], 3, 1.2, 6, 5, 9, 36],
        "circle-color": "#6b5b3e", "circle-blur": 0.6,
        "circle-opacity": ["interpolate", ["linear"], ["ln", ["+", ["get", "n"], 1]], 0, 0.04, 3, 0.18, 7, 0.5] } });
      map.addSource("fac", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "fac", type: "circle", source: "fac", paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 3, 8, 7],
        "circle-color": ["match", ["get", "state"], "certified", COLORS.certified, "fails", COLORS.fails, "indeterminate", COLORS.indeterminate, COLORS.unscored],
        // second channel: dark ring = interval wider than w_max; faded = certified on the prior alone (PRD 5.4, 5.4a)
        "circle-stroke-color": ["case", ["==", ["get", "precision"], "wide"], "#1d1d1b", "#ffffff"],
        "circle-stroke-width": ["case", ["==", ["get", "precision"], "wide"], 2, 1],
        "circle-opacity": ["case", ["all", ["==", ["get", "state"], "certified"], ["get", "prior_only"]], 0.4, 0.9] } });
      map.on("click", "fac", (e) => setSel(e.features[0].properties.id));
      // hover tooltip: both KPIs with their 90 % intervals (values are in the feature properties; no request needed)
      const popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 8, maxWidth: "320px" });
      map.on("mouseenter", "fac", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mousemove", "fac", (e) => {
        const q = e.features[0].properties; const isInt = q.scale === 1;
        const ip = isInt ? [q.p05, q.p50, q.p95] : [q.other_p05, q.other_p50, q.other_p95];
        const mp = isInt ? [q.other_p05, q.other_p50, q.other_p95] : [q.p05 * 1e-3, q.p50 * 1e-3, q.p95 * 1e-3];
        const w = halfWidth(q);
        popup.setLngLat(e.lngLat).setHTML(
          `<div style="font:12px/1.4 system-ui,sans-serif;color:#1d1d1b"><b>Facility #${q.id}</b> · ${q.basin} · ${q.facility_type}<br/>` +
          `<span style="color:${COLORS[q.state] || "#555"};font-weight:600">${q.state}</span> at the current bar · w ${fmtNum(w, 2)} (${q.precision})${q.prior_only ? " · prior-only" : ""}<br/>` +
          `intensity <b>${fmtPct(ip[1])}</b> <span style="color:#666">[${fmtPct(ip[0])} – ${fmtPct(ip[2])}]</span><br/>` +
          `absolute <b>${fmtNum(mp[1], 1)} t/yr</b> <span style="color:#666">[${fmtNum(mp[0], 1)} – ${fmtNum(mp[2], 1)} t/yr]</span><br/>` +
          (q.represents ? `<span style="color:#666">real site · ${fmtNum(q.boe_d, q.boe_d < 10 ? 1 : 0)} boe/d · stands for ${fmtNum(q.represents, 0)} sites like it</span><br/>` : "") +
          `<span style="color:#666">median and 90 % interval · click for the full panel</span></div>`).addTo(map);
      });
      map.on("mouseleave", "fac", () => { map.getCanvas().style.cursor = ""; popup.remove(); });
      mapRef.current = map;
      if (colored) map.getSource("fac").setData(colored);
      setReady(true);
    });
    return () => { map.remove(); mapRef.current = null; };
  }, []);

  useEffect(() => { const m = mapRef.current; if (m && m.getSource("fac") && colored) m.getSource("fac").setData(colored); }, [colored]);
  useEffect(() => { const m = mapRef.current; if (!m || !m.getSource("sites")) return;
    if (density) m.getSource("sites").setData(density.geo);
    m.setLayoutProperty("sites", "visibility", showDensity ? "visible" : "none"); }, [density, showDensity, ready]);

  return (
    <div className="map">
      <div ref={ref} style={{ position: "absolute", inset: 0 }} />
      <div className="legend">
        {["certified", "fails", "indeterminate"].map((s) => (<span key={s}><span className="dot" style={{ background: COLORS[s] }} />{s} ({counts[s]})</span>))}
        <span title="interval relative half-width w above w_max: certified, but the number is imprecise"><span className="dot ring" />wide w ({counts.wide})</span>
        <span title="certified, but the observations barely narrowed the prior"><span className="dot faded" style={{ background: COLORS.certified }} />prior-only ({counts.prior_only})</span>
        {density?.n > 0 && <label title="Every US production site with reported production (OGIM v3.0), counted per 0.1° cell. The dots are a sample of these sites; hover a dot to see how many it stands for.">
          <input type="checkbox" checked={showDensity} onChange={(e) => setShowDensity(e.target.checked)} /> all {fmtNum(density.n, 0)} real sites</label>}
        <span className="muted">{kpi === "intensity" ? `bar ${fmtPct(bar)}` : `bar ${fmtNum(bar)} t/yr`} at 95 %; w_max {fmtNum(wMax, 2)} · hover a dot for both KPIs</span>
      </div>
      {err && <div className="drill"><b>Could not load facilities.</b><div className="muted">{err}</div></div>}
      {!geo && !err && runId && <div className="drill muted">Loading facilities…</div>}
      {sel !== null && <Drilldown runId={runId} fid={sel} kpi={kpi} bar={bar} barIntensity={barIntensity} barMass={barMass} wMax={wMax} onClose={() => setSel(null)} />}
    </div>
  );
}
