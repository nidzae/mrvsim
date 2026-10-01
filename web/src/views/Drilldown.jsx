import React, { useEffect, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ScatterChart, Scatter, ZAxis, CartesianGrid } from "recharts";
import { api, waitForJob, fmtNum, fmtPct } from "../api.js";

const SENSOR_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];

// Interval bar against the bar line (PRD F9): p5-p95 band, p10/p90 ticks, median, truth (oracle, labelled).
function IntervalBar({ k, unit }) {
  const vals = [k.p05, k.p95, k.truth, k.bar].filter(Number.isFinite);
  const max = Math.max(...vals) * 1.15 || 1; const W = 380, H = 46, x = (v) => 10 + (v / max) * (W - 20);
  const color = { certified: "#0ca30c", fails: "#d03b3b", indeterminate: "#8d8b84" }[k.state] || "#888";
  return (
    <svg width={W} height={H} role="img" aria-label={`interval ${fmtNum(k.p05, 3)} to ${fmtNum(k.p95, 3)} ${unit}, bar ${fmtNum(k.bar, 3)}`}>
      <line x1={x(k.p05)} x2={x(k.p95)} y1={18} y2={18} stroke={color} strokeWidth={6} strokeLinecap="round" />
      <line x1={x(k.p10)} x2={x(k.p90)} y1={18} y2={18} stroke={color} strokeWidth={12} strokeLinecap="round" opacity={0.55} />
      <circle cx={x(k.p50)} cy={18} r={5} fill="#fff" stroke={color} strokeWidth={2} />
      {Number.isFinite(k.bar) && <line x1={x(k.bar)} x2={x(k.bar)} y1={4} y2={34} stroke="#0b0b0b" strokeDasharray="3 3" />}
      {Number.isFinite(k.truth) && <polygon points={`${x(k.truth) - 5},40 ${x(k.truth) + 5},40 ${x(k.truth)},31`} fill="#52514e" />}
      <text x={x(k.bar)} y={44} fontSize={10} textAnchor="middle" fill="#52514e">bar</text>
    </svg>
  );
}

export default function Drilldown({ runId, fid, kpi, onClose }) {
  const [d, setD] = useState(null); const [budget, setBudget] = useState(null); const [busy, setBusy] = useState(false); const [err, setErr] = useState(null);
  useEffect(() => { setD(null); setBudget(null); api.facility(runId, fid).then(setD).catch((e) => setErr(String(e))); }, [runId, fid]);
  const runBudget = async () => {
    setBusy(true); setErr(null);
    try { const j = await api.budget(runId, fid); setBudget(await waitForJob(j.job_id)); } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };
  if (err) return <div className="drill"><button className="ghost" onClick={onClose}>close</button><p className="flagged">{err}</p></div>;
  if (!d) return <div className="drill muted">Loading facility…</div>;
  const k = kpi === "intensity" ? d.intensity : d.mass_t_yr; const unit = kpi === "intensity" ? "" : "t/yr";
  const sensors = [...new Set(d.timeline.map((t) => t.sensor))];
  const pts = d.timeline.map((t) => ({ day: t.day, y: sensors.indexOf(t.sensor), kind: !t.usable ? (t.cloud_blocked ? "cloud-out" : t.sun_blocked ? "night" : "wind-out") : t.detected ? (t.false_positive ? "false positive" : "detection") : "non-detection", r: t.reported_kg_h, sensor: t.sensor }));
  const kinds = { detection: "#2a78d6", "non-detection": "#8d8b84", "cloud-out": "#c3c2b7", "wind-out": "#eda100", night: "#e5e4df", "false positive": "#e34948" };
  const budgetRows = budget ? Object.entries(budget.shares).map(([c, s]) => ({ component: c.replace("_", " "), share: s })) : [];
  return (
    <div className="drill">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <b>Facility #{d.id}</b><span className={`badge ${k.state}`}>{k.state}</span><button className="ghost" onClick={onClose}>close</button>
      </div>
      <div className="muted">{d.basin} · {d.facility_type} · {d.n_sources} source{d.n_sources > 1 ? "s" : ""} · posterior: {d.method}</div>
      <h3>{kpi === "intensity" ? "Methane intensity" : "Absolute emissions (t/yr)"}</h3>
      <IntervalBar k={k} unit={unit} />
      <table className="grid"><tbody>
        <tr><th>p5</th><th>p10</th><th>median</th><th>p90 (cert. bound)</th><th>p95</th><th>truth</th></tr>
        <tr>{[k.p05, k.p10, k.p50, k.p90, k.p95, k.truth].map((v, i) => <td key={i} className="num">{kpi === "intensity" ? fmtPct(v) : fmtNum(v, 1)}</td>)}</tr>
      </tbody></table>
      <h3>Observation timeline</h3>
      <div style={{ height: 40 + 22 * sensors.length }}>
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 6, right: 10, left: 0, bottom: 4 }}>
            <CartesianGrid stroke="var(--border)" vertical={false} />
            <XAxis type="number" dataKey="day" domain={[0, 365]} tickCount={7} tick={{ fontSize: 11 }} stroke="var(--text-3)" />
            <YAxis type="number" dataKey="y" domain={[-0.5, sensors.length - 0.5]} ticks={sensors.map((_, i) => i)} tickFormatter={(i) => sensors[i]} width={90} tick={{ fontSize: 11 }} stroke="var(--text-3)" />
            <ZAxis range={[30, 30]} />
            <Tooltip cursor={false} content={({ payload }) => payload?.length ? <div className="panel" style={{ padding: 6 }}>{`day ${payload[0].payload.day.toFixed(1)} · ${payload[0].payload.sensor} · ${payload[0].payload.kind}${payload[0].payload.r ? ` · ${fmtNum(payload[0].payload.r)} kg/h` : ""}`}</div> : null} />
            {Object.keys(kinds).map((kind) => <Scatter key={kind} name={kind} data={pts.filter((p) => p.kind === kind)} fill={kinds[kind]} />)}
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <div className="legend">{Object.entries(kinds).map(([kname, c]) => <span key={kname}><span className="dot" style={{ background: c }} />{kname}</span>)}</div>
      {Object.keys(d.cms).length > 0 && <div className="muted" style={{ marginTop: 6 }}>CMS: {Object.entries(d.cms).map(([s, c]) => `${s} detected share ${fmtPct(c.detected_share, 1)}`).join("; ")}</div>}
      <h3>Variance budget (oracle ablation, TDD §6.8)</h3>
      {!budget && <button className="primary" disabled={busy} onClick={runBudget}>{busy ? "computing…" : "Compute variance budget"}</button>}
      {budget && (
        <div style={{ height: 180 }}>
          <ResponsiveContainer>
            <BarChart data={budgetRows} layout="vertical" margin={{ left: 10, right: 30 }}>
              <XAxis type="number" domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} tick={{ fontSize: 11 }} stroke="var(--text-3)" />
              <YAxis type="category" dataKey="component" width={130} tick={{ fontSize: 11 }} stroke="var(--text-3)" />
              <Tooltip formatter={(v) => `${(v * 100).toFixed(0)} %`} />
              <Bar dataKey="share" fill="#2a78d6" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
      <details style={{ marginTop: 8 }}><summary className="muted">Oracle truth (synthetic; never seen by the estimator)</summary>
        <table className="grid"><thead><tr><th>source</th><th className="num">q kg/h</th><th>type</th><th className="num">π</th><th className="num">on hours</th></tr></thead>
          <tbody>{d.oracle_truth_sources.map((s, i) => <tr key={i}><td>{i + 1}</td><td className="num">{fmtNum(s.q_kg_h, 2)}</td><td>{s.intermittent ? "intermittent" : "steady"}</td><td className="num">{fmtNum(s.pi, 3)}</td><td className="num">{s.on_hours}</td></tr>)}</tbody></table>
      </details>
    </div>
  );
}
