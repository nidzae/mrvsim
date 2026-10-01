import React, { useEffect, useState } from "react";
import { api } from "./api.js";
import MapView from "./views/MapView.jsx";
import Dashboard from "./views/Dashboard.jsx";
import Sensors from "./views/Sensors.jsx";
import Optimize from "./views/Optimize.jsx";
import GapAnalysis from "./views/GapAnalysis.jsx";
import Attribution from "./views/Attribution.jsx";
import Validation from "./views/Validation.jsx";
import QuickStart from "./views/QuickStart.jsx";

const TABS = ["Map", "Dashboard", "Optimize", "Gap analysis", "Attribution", "Validation"];
const DEFAULT_POLICY = { sensors: { bridger_gml: { coverage: 1.0, frequency_per_year: 2, targeting: "random" }, ghgsat_c: { coverage: 0.3, frequency_per_year: 12, targeting: "throughput" }, tropomi: { coverage: 1.0, frequency_per_year: 1, targeting: "random" } } };

// Minimal YAML reader for the policy proposals the gap-analysis model returns (sensors: {key: {coverage, frequency_per_year, targeting}}).
function parsePolicyYaml(text) {
  const out = { sensors: {} }; let cur = null;
  for (const raw of text.split("\n")) {
    const line = raw.replace(/#.*$/, "").trimEnd(); if (!line.trim()) continue;
    const indent = line.match(/^\s*/)[0].length; const m = line.trim().match(/^([A-Za-z0-9_\-]+):\s*(.*)$/); if (!m) continue;
    const [, key, val] = m;
    if (indent === 0 && key === "sensors") continue;
    if (indent > 0 && val === "" ) { cur = key; out.sensors[cur] = { coverage: 0.2, frequency_per_year: 1, targeting: "random" }; continue; }
    if (indent > 0 && val.startsWith("{")) { cur = key; const o = {}; val.replace(/[{}]/g, "").split(",").forEach((kv) => { const [k, v] = kv.split(":").map((s) => s.trim()); if (k) o[k] = isNaN(+v) ? v : +v; }); out.sensors[cur] = { coverage: 0.2, frequency_per_year: 1, targeting: "random", ...o }; continue; }
    if (cur && indent > 0) out.sensors[cur][key] = isNaN(+val) ? val.replace(/['"]/g, "") : +val;
  }
  return out;
}

export default function App() {
  const [tab, setTab] = useState("Map"); const [help, setHelp] = useState(false);
  const [runId, setRunId] = useState(null); const [runs, setRuns] = useState([]);
  const [kpi, setKpi] = useState("intensity"); const [bar, setBar] = useState(0.002); const [barMass, setBarMass] = useState(50); const [wMax, setWMax] = useState(0.3);
  const [policy, setPolicy] = useState(DEFAULT_POLICY);
  const [runSettings, setRunSettings] = useState({ name: "interactive", mode: "quick", seed: 20260930, replications: 3, n_draws: 2000, facilities_per_stratum: 10, n_per_stratum: 30 });
  const [busy, setBusy] = useState(false); const [pareto, setPareto] = useState(null); const [health, setHealth] = useState(null);
  useEffect(() => { api.runs().then((r) => { setRuns(r.runs); const first = r.runs.find((x) => x.has_summary && !/\[|v4-|v7-|tornado|opt-/.test(x.name || "")); if (first) setRunId(first.run_id); else { const any = r.runs.find((x) => x.has_summary); if (any) setRunId(any.run_id); } }).catch(() => {}); api.health().then(setHealth).catch(() => {}); }, []);
  const scoring = { bar_intensity: bar, bar_mass_t_yr: barMass, w_max: wMax };
  const applyYaml = (yaml) => { try { setPolicy(parsePolicyYaml(yaml)); setTab("Map"); } catch (e) { alert(`Could not parse the proposal: ${e}`); } };
  const applyPolicy = (p) => { setPolicy({ sensors: Object.fromEntries(Object.entries(p.sensors).filter(([, s]) => s.enabled !== false).map(([k, s]) => [k, { coverage: s.coverage, frequency_per_year: s.frequency_per_year, targeting: s.targeting }])) }); setTab("Map"); };
  const onRunDone = (id) => { setRunId(id); api.runs().then((r) => setRuns(r.runs)).catch(() => {}); };
  const [fullRequest, setFullRequest] = useState(null);
  const rerunFull = (cfg) => { setPolicy({ sensors: cfg.policy.sensors }); setFullRequest({ seed: cfg.seed, scoring: cfg.scoring }); setRunSettings((r) => ({ ...r, mode: "full", seed: cfg.seed })); setTab("Map"); };
  const wide = tab === "Validation" || tab === "Attribution";
  return (
    <div className="app">
      <div className="topbar">
        <h1>MRVSim</h1>
        <div className="tabs">{TABS.map((t) => <button key={t} className={`tab${tab === t ? " active" : ""}`} onClick={() => setTab(t)}>{t}</button>)}</div>
        <div className="spacer" />
        <div className="barctl">
          <select value={kpi} onChange={(e) => setKpi(e.target.value)}><option value="intensity">intensity</option><option value="mass">absolute (t/yr)</option></select>
          {kpi === "intensity" ? <>bar %<input type="number" step="0.05" min="0" value={+(bar * 100).toFixed(3)} onChange={(e) => setBar(+e.target.value / 100)} /></> : <>bar t/yr<input type="number" step="5" min="0" value={barMass} onChange={(e) => setBarMass(+e.target.value)} /></>}
          precision w<input type="number" step="0.05" min="0.05" value={wMax} onChange={(e) => setWMax(+e.target.value)} />
          <select value={runId || ""} onChange={(e) => setRunId(e.target.value)} style={{ maxWidth: 220 }}>{!runs.length && <option value="">no runs</option>}{runs.filter((r) => r.has_summary && !/^(v4-|v7-|tornado-|opt-)/.test(r.name || "")).map((r) => <option key={r.run_id} value={r.run_id}>{r.name} · {r.run_id}</option>)}</select>
        </div>
        <button className="help" title="Quick start" onClick={() => setHelp(true)}>?</button>
      </div>
      <div className={`main${wide ? " wide" : ""}`}>
        {!wide && <div className="side"><Sensors policy={policy} setPolicy={setPolicy} runSettings={{ ...runSettings, scoring }} setRunSettings={setRunSettings} onRunDone={onRunDone} busy={busy} setBusy={setBusy} />
          {health && !health.anthropic_key_configured && <p className="muted" style={{ marginTop: 10 }}>Gap analysis: the API server has no Anthropic credentials (set ANTHROPIC_API_KEY or run `ant auth login` where the server runs).</p>}</div>}
        <div className="content">
          {tab === "Map" && <MapView runId={runId} kpi={kpi} bar={kpi === "intensity" ? bar : barMass} wMax={wMax} />}
          {tab === "Dashboard" && <Dashboard runId={runId} kpi={kpi} runSettings={{ ...runSettings, mode: "quick", policy, scoring }} pareto={pareto} onRerunFull={rerunFull} />}
          {tab === "Optimize" && <Optimize scoring={scoring} onPareto={setPareto} onApply={applyPolicy} />}
          {tab === "Gap analysis" && <GapAnalysis runId={runId} onApply={applyYaml} />}
          {tab === "Attribution" && <Attribution runId={runId} />}
          {tab === "Validation" && <Validation />}
        </div>
      </div>
      {help && <QuickStart onClose={() => setHelp(false)} />}
    </div>
  );
}
