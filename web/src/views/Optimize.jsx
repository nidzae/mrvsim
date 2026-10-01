import React, { useState } from "react";
import { api, waitForJob, fmtNum, fmtPct, fmtUsd } from "../api.js";

// Optimize: find the cheapest mix meeting (bar, precision, certified share) (PRD F8, G4; QUICKSTART "Optimize").
export default function Optimize({ scoring, onPareto, onApply }) {
  const [opts, setOpts] = useState({ n_trials: 8, kappa_min: 0.85, w_max: scoring.w_max, theta: 0.5, kpi: "intensity", replications_trial: 1, replications_full: 2, n_draws: 1000, facilities_per_stratum: 5,
    sensors: ["bridger_gml", "ghgsat_c", "cms_generic"], fixed: { tropomi: { coverage: 1.0 } } });
  const [busy, setBusy] = useState(false); const [res, setRes] = useState(null); const [err, setErr] = useState(null);
  const run = async () => {
    setBusy(true); setErr(null);
    try { const j = await api.optimize({ ...opts, scoring: { ...scoring, w_max: opts.w_max } }); const r = await waitForJob(j.job_id); setRes(r); onPareto(r); } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };
  const F = ({ k, label, step = 0.05, min = 0, max = 1 }) => <div className="field">{label}<input type="number" step={step} min={min} max={max} value={opts[k]} onChange={(e) => setOpts({ ...opts, [k]: +e.target.value })} /></div>;
  return (
    <div className="row">
      <div className="panel" style={{ width: 320 }}>
        <h2>Find frontier</h2>
        <F k="w_max" label="precision w_max" />
        <F k="theta" label="certified throughput share ≥ θ" />
        <F k="kappa_min" label="calibration κ ≥" step={0.01} />
        <F k="n_trials" label="trials" step={1} min={2} max={200} />
        <F k="replications_trial" label="R per trial" step={1} min={1} max={50} />
        <F k="replications_full" label="R for re-scoring" step={1} min={1} max={200} />
        <F k="facilities_per_stratum" label="facilities / stratum" step={1} min={1} max={100} />
        <F k="n_draws" label="posterior draws" step={100} min={200} max={20000} />
        <div className="field">search over<input value={opts.sensors.join(",")} onChange={(e) => setOpts({ ...opts, sensors: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} style={{ width: 170 }} /></div>
        <p className="muted">Bar: {opts.kpi === "intensity" ? fmtPct(scoring.bar_intensity) : `${scoring.bar_mass_t_yr} t/yr`}. Each trial is a full pipeline run at reduced R; the top candidates are re-scored at full R (TDD §8.3).</p>
        <button className="primary" disabled={busy} onClick={run}>{busy ? "Optimizing… (minutes)" : "Find frontier"}</button>
        {err && <p className="flagged">{err}</p>}
      </div>
      <div className="panel" style={{ flex: 1, minWidth: 380 }}>
        <h2>Pareto set (feasible, non-dominated)</h2>
        {!res && <p className="muted">No study yet. The frontier chart appears on the Dashboard once a study finishes.</p>}
        {res && (<>
          <p className="muted">{res.all_trials.length} trials · {res.pareto.length} on the frontier · {res.rescored_top.length} re-scored at R = {res.meta.replications_full}</p>
          <table className="grid"><thead><tr><th>trial</th><th className="num">cost (sample)</th><th className="num">median w</th><th className="num">κ</th><th className="num">cert. share</th><th>policy</th><th></th></tr></thead>
            <tbody>{(res.rescored_top.length ? res.rescored_top : res.pareto).map((t) => (
              <tr key={t.number}><td>{t.number}{t.rescored ? " ★" : ""}</td><td className="num">{fmtUsd(t.cost_usd)}</td><td className="num">{fmtNum(t.width, 2)}</td><td className="num">{fmtNum(t.calibration, 2)}</td><td className="num">{fmtPct(t.certified_share_throughput, 0)}</td>
                <td className="muted" style={{ fontSize: 12 }}>{Object.entries(t.policy.sensors).filter(([, s]) => s.enabled).map(([k, s]) => `${k} ${Math.round(s.coverage * 100)}%×${s.frequency_per_year}`).join("; ")}</td>
                <td><button className="ghost" onClick={() => onApply(t.policy)}>load</button></td></tr>))}</tbody></table>
          {!res.pareto.length && <p className="flagged">No trial satisfied the constraints at these settings.</p>}
        </>)}
      </div>
    </div>
  );
}
