import React, { useEffect, useMemo, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import { api, classify, fmtNum, fmtPct } from "../api.js";
import Drilldown from "./Drilldown.jsx";

const COLORS = { certified: "#0ca30c", fails: "#d03b3b", indeterminate: "#8d8b84", unscored: "#555" };
const STYLE = {
  version: 8,
  sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap contributors" } },
  layers: [{ id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.7, "raster-brightness-max": 0.9 } }],
};

// Facility map with three-state colouring at the user's (B, w_max) (PRD F9). Synthetic coordinates: representative, not real assets.
export default function MapView({ runId, kpi, bar, wMax }) {
  const ref = useRef(null); const mapRef = useRef(null);
  const [geo, setGeo] = useState(null); const [sel, setSel] = useState(null); const [err, setErr] = useState(null);

  useEffect(() => { if (!runId) return; setGeo(null); api.facilities(runId, kpi).then(setGeo).catch((e) => setErr(String(e))); }, [runId, kpi]);

  const colored = useMemo(() => {
    if (!geo) return null;
    const B = kpi === "intensity" ? bar : bar * 1000; // mass bar in t/yr -> kg/yr
    return { ...geo, features: geo.features.map((f) => ({ ...f, properties: { ...f.properties, state: classify(f.properties, B, wMax) } })) };
  }, [geo, bar, wMax, kpi]);

  const counts = useMemo(() => {
    const c = { certified: 0, fails: 0, indeterminate: 0 };
    colored?.features.forEach((f) => { if (c[f.properties.state] !== undefined) c[f.properties.state]++; });
    return c;
  }, [colored]);

  useEffect(() => {
    if (mapRef.current || !ref.current) return;
    const map = new maplibregl.Map({ container: ref.current, style: STYLE, center: [-98, 37], zoom: 3.6, attributionControl: true });
    map.addControl(new maplibregl.NavigationControl(), "top-left");
    map.on("load", () => {
      map.addSource("fac", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "fac", type: "circle", source: "fac", paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 3, 8, 7],
        "circle-color": ["match", ["get", "state"], "certified", COLORS.certified, "fails", COLORS.fails, "indeterminate", COLORS.indeterminate, COLORS.unscored],
        "circle-stroke-color": "#ffffff", "circle-stroke-width": 1, "circle-opacity": 0.9 } });
      map.on("click", "fac", (e) => setSel(e.features[0].properties.id));
      map.on("mouseenter", "fac", () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", "fac", () => (map.getCanvas().style.cursor = ""));
      mapRef.current = map;
      if (colored) map.getSource("fac").setData(colored);
    });
    return () => { map.remove(); mapRef.current = null; };
  }, []);

  useEffect(() => { const m = mapRef.current; if (m && m.getSource("fac") && colored) m.getSource("fac").setData(colored); }, [colored]);

  return (
    <div className="map">
      <div ref={ref} style={{ position: "absolute", inset: 0 }} />
      <div className="legend">
        {["certified", "fails", "indeterminate"].map((s) => (<span key={s}><span className="dot" style={{ background: COLORS[s] }} />{s} ({counts[s]})</span>))}
        <span className="muted">{kpi === "intensity" ? `bar ${fmtPct(bar)}` : `bar ${fmtNum(bar)} t/yr`}, w ≤ {fmtNum(wMax, 2)}</span>
      </div>
      {err && <div className="drill"><b>Could not load facilities.</b><div className="muted">{err}</div></div>}
      {!geo && !err && runId && <div className="drill muted">Loading facilities…</div>}
      {sel !== null && <Drilldown runId={runId} fid={sel} kpi={kpi} onClose={() => setSel(null)} />}
    </div>
  );
}
