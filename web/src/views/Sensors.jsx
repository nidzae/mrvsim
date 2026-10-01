import React, { useEffect, useState } from "react";
import { api, waitForJob } from "../api.js";

const TIERS = ["A", "B", "C", "D"];

// Per-sensor controls: on/off, coverage, frequency, targeting, validation-tier filter; Run (PRD F6, QUICKSTART "Sensors").
export default function Sensors({ policy, setPolicy, runSettings, setRunSettings, onRunStarted, onRunDone, busy, setBusy }) {
  const [lib, setLib] = useState([]); const [minTier, setMinTier] = useState("A"); const [showAdv, setShowAdv] = useState(false); const [err, setErr] = useState(null); const [prog, setProg] = useState(null);
  useEffect(() => { api.sensors().then((r) => setLib(r.sensors)).catch((e) => setErr(String(e))); }, []);
  const get = (k) => policy.sensors[k];
  const upd = (k, patch) => setPolicy({ ...policy, sensors: { ...policy.sensors, [k]: { ...(get(k) || { coverage: 0.2, frequency_per_year: 1, targeting: "random" }), ...patch } } });
  const toggle = (k) => { const s = { ...policy.sensors }; if (s[k]) delete s[k]; else s[k] = { coverage: 0.2, frequency_per_year: lib.find((x) => x.key === k)?.schedule === "orbit" ? 12 : 1, targeting: "random" }; setPolicy({ ...policy, sensors: s }); };
  const [mode, setMode] = useState(runSettings.mode || "quick"); const [est, setEst] = useState(null);
  useEffect(() => { if (runSettings.mode && runSettings.mode !== mode) setMode(runSettings.mode); }, [runSettings.mode]);
  useEffect(() => {
    const body = { ...runSettings, mode, policy: { ...policy, min_tier: minTier } };
    api.estimate(body).then(setEst).catch(() => setEst(null));
  }, [mode, policy, runSettings, minTier]);
  const fmtTime = (sec) => (sec < 90 ? `~${Math.max(10, Math.round(sec / 10) * 10)} s` : sec < 5400 ? `~${Math.round(sec / 60)} min` : `~${(sec / 3600).toFixed(1)} h`);
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const body = { ...runSettings, mode, policy: { ...policy, min_tier: minTier } };
      const j = await api.startRun(body); onRunStarted && onRunStarted(j);
      const res = await waitForJob(j.job_id, (jj) => setProg(jj.progress));
      onRunDone(res.run_id);
    } catch (e) { setErr(String(e)); } finally { setBusy(false); setProg(null); }
  };
  const visible = lib.filter((s) => TIERS.indexOf(s.tier) <= TIERS.indexOf(minTier) || policy.sensors[s.key]);
  return (
    <div>
      <h2 style={{ fontSize: 14, margin: "4px 0 8px" }}>Sensors</h2>
      {visible.map((s) => { const on = !!get(s.key); return (
        <div className="sensor" key={s.key}>
          <div className="name"><label><input type="checkbox" checked={on} onChange={() => toggle(s.key)} /> {s.key}</label><span className={`tier ${s.tier}`} title={s.enabled_for_certification ? "counts toward certification" : "excluded from certification (PRD N2)"}>Tier {s.tier}</span></div>
          <div className="muted">{s.name} · POD50 {s.pod50_kg_h >= 100 ? `${(s.pod50_kg_h / 1000).toFixed(1)} t/h` : `${s.pod50_kg_h.toFixed(2)} kg/h`}{s.unverified_blocks.length ? " · parameters unverified" : ""}</div>
          {on && <>
            <div className="field">coverage (share of facilities)<input type="number" min="0" max="1" step="0.05" value={get(s.key).coverage} onChange={(e) => upd(s.key, { coverage: +e.target.value })} /></div>
            {(s.schedule === "campaign" || s.schedule === "survey" || s.key === "ghgsat_c" || s.key === "prisma" || s.key === "enmap" || s.key === "tanager1") && (
              <div className="field">{s.schedule === "orbit" ? "taskings / year" : "surveys / year"}<input type="number" min="0" max="52" step="1" value={get(s.key).frequency_per_year} onChange={(e) => upd(s.key, { frequency_per_year: +e.target.value })} /></div>)}
            <div className="field">targeting<select value={get(s.key).targeting} onChange={(e) => upd(s.key, { targeting: e.target.value })}><option value="random">random</option><option value="throughput">throughput-weighted</option></select></div>
          </>}
        </div>); })}
      <div className="sensor"><button className="ghost" onClick={() => setShowAdv(!showAdv)}>Advanced {showAdv ? "▴" : "▾"}</button>
        {showAdv && <>
          <div className="field">validation-tier filter<select value={minTier} onChange={(e) => setMinTier(e.target.value)}>{TIERS.map((t) => <option key={t}>{t}</option>)}</select></div>
          <div className="field">facilities / stratum<input type="number" min="1" max="100" value={runSettings.facilities_per_stratum ?? 10} onChange={(e) => setRunSettings({ ...runSettings, facilities_per_stratum: +e.target.value })} /></div>
          <div className="field">posterior draws<input type="number" min="200" step="100" value={runSettings.n_draws} onChange={(e) => setRunSettings({ ...runSettings, n_draws: +e.target.value })} /></div>
          <div className="field">replications R<input type="number" min="1" max="200" value={runSettings.replications} onChange={(e) => setRunSettings({ ...runSettings, replications: +e.target.value })} /></div>
          <div className="field">seed<input type="number" value={runSettings.seed} onChange={(e) => setRunSettings({ ...runSettings, seed: +e.target.value })} /></div>
          <div className="muted">These apply in <b>custom</b> mode. Quick and Full override them.</div>
        </>}
      </div>
      <div className="sensor">
        <div className="name">Compute mode</div>
        <div className="seg">
          {["quick", "full", "custom"].map((m) => <button key={m} className={`segbtn${mode === m ? " active" : ""}`} onClick={() => setMode(m)}>{m}</button>)}
        </div>
        <div className="muted">
          {mode === "quick" && "Speed: 10 facilities per stratum, 2,000 draws, 3 replications. For exploring sensor mixes."}
          {mode === "full" && "Precision: every facility of the default sample (100 per stratum), 10,000 draws, 5 replications. For a mix you want to trust."}
          {mode === "custom" && "Uses the Advanced settings below."}
          {est && <> Estimated {fmtTime(est.estimated_seconds)} for {est.facilities_estimated.toLocaleString()} facilities × {est.replications} replications.</>}
        </div>
      </div>
      <div style={{ marginTop: 10 }}><button className="primary" disabled={busy} onClick={run}>{busy ? `Running ${prog?.mode || mode}… ${prog?.stage || ""}` : `Run ${mode}`}</button></div>
      {err && <p className="flagged">{err}</p>}
    </div>
  );
}
