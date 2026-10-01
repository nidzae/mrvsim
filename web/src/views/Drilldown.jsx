import React, { useEffect, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ScatterChart, Scatter, ZAxis, CartesianGrid } from "recharts";
import { api, waitForJob, classify, halfWidth, precision, probBelow, fmtProb, fmtNum, fmtPct } from "../api.js";

const SENSOR_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];

// Interval bar against the bar line (PRD F9): prior band (faint, above), posterior p5-p95 band, p10/p90 band, median, truth (oracle, labelled).
function IntervalBar({ k, unit }) {
  const pr = k.prior;
  const vals = [k.p05, k.p95, k.truth, k.bar, pr?.p95].filter(Number.isFinite);
  const max = Math.max(...vals) * 1.15 || 1; const W = 380, H = 58, x = (v) => 10 + (Math.min(v, max) / max) * (W - 20);
  const color = { certified: "#0ca30c", fails: "#d03b3b", indeterminate: "#8d8b84" }[k.state] || "#888";
  return (
    <svg className="interval" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`interval ${fmtNum(k.p05, 3)} to ${fmtNum(k.p95, 3)} ${unit}, bar ${fmtNum(k.bar, 3)}`}>
      {pr && <>
        <line x1={x(pr.p05)} x2={x(pr.p95)} y1={8} y2={8} stroke="#b9b8b0" strokeWidth={4} strokeLinecap="round" strokeDasharray="2 3" />
        <text x={x(pr.p05)} y={5} fontSize={8} fill="#8f8e86">prior</text>
      </>}
      <line x1={x(k.p05)} x2={x(k.p95)} y1={30} y2={30} stroke={color} strokeWidth={6} strokeLinecap="round" />
      <line x1={x(k.p10)} x2={x(k.p90)} y1={30} y2={30} stroke={color} strokeWidth={12} strokeLinecap="round" opacity={0.55} />
      <circle cx={x(k.p50)} cy={30} r={5} fill="#fff" stroke={color} strokeWidth={2} />
      {Number.isFinite(k.bar) && <line x1={x(k.bar)} x2={x(k.bar)} y1={14} y2={46} stroke="#0b0b0b" strokeDasharray="3 3" />}
      {Number.isFinite(k.truth) && <polygon points={`${x(k.truth) - 5},52 ${x(k.truth) + 5},52 ${x(k.truth)},43`} fill="#52514e" />}
      <text x={x(k.bar)} y={56} fontSize={10} textAnchor="middle" fill="#52514e">bar</text>
    </svg>
  );
}

// One KPI block: decision at its bar, probability, interval bar, percentile table, precision line.
function KpiBlock({ name, raw, bar, wMax, isInt, primary }) {
  const k = { ...raw, bar, state: classify(raw, bar), precision: precision(raw, wMax) };
  const w = halfWidth(k); const pb = probBelow(raw, bar);
  const fmtK = (v) => (isInt ? fmtPct(v) : `${fmtNum(v, 1)} t/yr`);
  const f = (v) => (isInt ? fmtPct(v) : fmtNum(v, 1));
  return (
    <div style={primary ? {} : { opacity: 0.92, borderTop: "1px solid var(--border)", marginTop: 10, paddingTop: 6 }}>
      <h3>{name}{!primary && <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}> · the other KPI, decided at its own bar ({fmtK(bar)})</span>}</h3>
      <div className="prob"><b>{fmtProb(pb)}</b> posterior probability of being below the bar ({fmtK(bar)}) <span className={`badge ${k.state}`} style={{ marginLeft: 6 }}>{k.state}</span></div>
      <div className="muted">{decision}</div>
      <IntervalBar k={k} unit={isInt ? "" : "t/yr"} />
      <table className="grid"><tbody>
        <tr><th></th><th>p5 (fail bound)</th><th>p10</th><th>median</th><th>p90</th><th>p95 (cert. bound)</th><th>truth</th></tr>
        <tr><th>posterior</th>{[k.p05, k.p10, k.p50, k.p90, k.p95, k.truth].map((v, i) => <td key={i} className="num">{f(v)}</td>)}</tr>
        {k.prior && <tr><th>prior (no data)</th><td className="num">{f(k.prior.p05)}</td><td /><td className="num">{f(k.prior.p50)}</td><td /><td className="num">{f(k.prior.p95)}</td><td /></tr>}
      </tbody></table>
      <div className="muted" style={{ marginTop: 4 }}>
        <b>Precision:</b> w = (p95 − p5) / (2 · median) = {fmtNum(w, 2)} {k.precision === "precise" ? "≤" : ">"} w_max {fmtNum(wMax, 2)} ({k.precision}). Does not affect the decision.
      </div>
    </div>
  );
}

export default function Drilldown({ runId, fid, kpi, bar, barIntensity, barMass, wMax, onClose }) {
  const [d, setD] = useState(null); const [budget, setBudget] = useState(null); const [busy, setBusy] = useState(false); const [err, setErr] = useState(null);
  useEffect(() => { setD(null); setBudget(null); api.facility(runId, fid).then(setD).catch((e) => setErr(String(e))); }, [runId, fid]);
  const runBudget = async () => {
    setBusy(true); setErr(null);
    try { const j = await api.budget(runId, fid); setBudget(await waitForJob(j.job_id)); } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };
  const stop = { onWheel: (e) => e.stopPropagation(), onTouchMove: (e) => e.stopPropagation() };
  if (err) return <div className="drill" {...stop}><button className="ghost" onClick={onClose}>close</button><p className="flagged">{err}</p></div>;
  if (!d) return <div className="drill muted" {...stop}>Loading facility…</div>;
  // Decide at the live bar from the top bar so the badge, the bar line and the map agree (PRD F9; DECISION_LOG 2026-10-01).
  const raw = kpi === "intensity" ? d.intensity : d.mass_t_yr; const unit = kpi === "intensity" ? "" : "t/yr";
  const ev = d.evidence || null;
  const ratio = ev ? (kpi === "intensity" ? ev.prior_ratio_intensity : ev.prior_ratio_mass) : null;
  const nObs = ev ? ev.n_usable_snapshots + ev.n_survey_visits : null;
  const priorOnly = ev ? ((Number.isFinite(ratio) && ratio > ev.prior_only_ratio) || (nObs === 0 && ev.cms_usable_hours === 0)) : false;
  const k = { ...raw, bar, state: classify(raw, bar), precision: precision(raw, wMax), priorOnly };
  const w = halfWidth(k); const pb = probBelow(raw, bar);
  const fmtK = (v) => (kpi === "intensity" ? fmtPct(v) : `${fmtNum(v, 1)} ${unit}`);
  const legacy = !raw.quantiles;   // scored before the quantile grid and evidence measures existed (2026-10-01)
  const thr = d.throughput; const mon = d.monitoring || [];
  const sensors = [...new Set(d.timeline.map((t) => t.sensor))];
  const baseKind = (t) => (!t.usable ? (t.cloud_blocked ? "cloud-out" : t.sun_blocked ? "night" : "wind-out") : t.detected ? (t.false_positive ? "false positive" : "detection") : "non-detection");
  const pts = d.timeline.map((t) => { const k = baseKind(t); return { day: t.day, y: sensors.indexOf(t.sensor), kind: t.incidental && (k === "detection" || k === "non-detection") ? `incidental ${k}` : k, r: t.reported_kg_h, sensor: t.sensor, scene: t.scene_target }; });
  const kinds = { detection: "#2a78d6", "non-detection": "#8d8b84", "incidental detection": "#8fb8ea", "incidental non-detection": "#cfceca", "cloud-out": "#c3c2b7", "wind-out": "#eda100", night: "#e5e4df", "false positive": "#e34948" };
  const budgetRows = budget ? Object.entries(budget.shares).map(([c, s]) => ({ component: c.replace("_", " "), share: s })) : [];
  return (
    <div className="drill" {...stop}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", position: "sticky", top: -12, background: "var(--surface)", padding: "4px 0", zIndex: 1 }}>
        <b>Facility #{d.id}</b>
        <span style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <span className={`badge ${k.state}`}>{k.state}</span>
          <span className={`badge ${k.precision}`} title={`relative half-width w = ${fmtNum(w, 2)} vs w_max ${fmtNum(wMax, 2)}`}>{k.precision === "precise" ? "precise" : "wide"} · w {fmtNum(w, 2)}</span>
          {k.priorOnly && <span className="badge prior-only" title="the observations barely narrowed the prior; this certification rests on the population prior">prior-only</span>}
        </span>
        <button className="ghost" onClick={onClose}>close</button>
      </div>
      <div className="muted">{d.basin} · {d.facility_type} · {d.n_sources} source{d.n_sources > 1 ? "s" : ""} · posterior: {d.method}
        {thr && <> · gas throughput rank <b>{thr.rank}</b> of {thr.n_facilities} ({fmtNum(thr.gas_mkt_m3_yr / 1e6, 1)} Mm³/yr)</>}</div>
      {legacy && <div className="muted" style={{ marginTop: 4 }}>This run was scored before the probability grid and evidence measures existed; probabilities outside p5–p95 are bounds. Re-run it to get exact values.</div>}
      <KpiBlock name={kpi === "intensity" ? "Methane intensity" : "Absolute emissions (t/yr)"} raw={raw} bar={bar} wMax={wMax} isInt={kpi === "intensity"} primary />
      {ev && <div className="muted" style={{ marginTop: 4 }}>
        <b>Evidence:</b> {ev.n_usable_snapshots} usable snapshot{ev.n_usable_snapshots === 1 ? "" : "s"} · {ev.n_survey_visits} survey visit{ev.n_survey_visits === 1 ? "" : "s"} · {ev.cms_usable_hours.toLocaleString()} CMS hours {ev.n_incidental_usable > 0 && <> (of which {ev.n_incidental_usable} incidental)</>} · posterior interval is {Number.isFinite(ratio) ? `${Math.round(ratio * 100)} %` : "–"} as wide as the prior's (log scale; prior-only above {Math.round(ev.prior_only_ratio * 100)} %).
        {k.priorOnly && <span className="flagged"> Prior-only: the decision rests on the population prior, not on measurements of this facility.</span>}
      </div>}
      <KpiBlock name={kpi === "intensity" ? "Absolute emissions (t/yr)" : "Methane intensity"} raw={kpi === "intensity" ? d.mass_t_yr : d.intensity}
                bar={kpi === "intensity" ? (barMass ?? d.mass_t_yr.bar) : (barIntensity ?? d.intensity.bar)} wMax={wMax} isInt={kpi !== "intensity"} primary={false} />
      {mon.length > 0 && <>
        <h3>Monitoring at this facility</h3>
        <table className="grid"><tbody>
          <tr><th>sensor</th><th>policy rule</th><th>this facility</th></tr>
          {mon.map((m) => <tr key={m.sensor} className={m.covered ? "" : "muted"}>
            <td>{m.sensor}</td>
            <td>{m.rule}<div className="muted">{m.how}</div></td>
            <td><b className={m.covered ? "why certified" : "why indeterminate"}>{m.reason}</b>{m.planned_visit_days.length > 0 && <div className="muted">visit days {m.planned_visit_days.join(", ")}</div>}
              {m.incidental_looks > 0 && <div className="muted">seen incidentally {m.incidental_looks}× ({m.incidental_usable} usable) inside scenes framed on {m.incidental_from.length === 1 ? `facility #${m.incidental_from[0]}` : `${m.incidental_from.length} neighbours`}</div>}</td>
          </tr>)}
        </tbody></table>
        <div className="muted" style={{ marginTop: 4 }}>Sensors are assigned per facility by coverage share and targeting; location plays no part in the assignment. A tasked satellite scene or an aircraft survey block also captures any neighbour inside it ("incidental"); "regional campaign" flies each basin within one window of days.</div>
      </>}
      <h3>Observation timeline</h3>
      <div style={{ height: 40 + 22 * sensors.length }}>
        <ResponsiveContainer>
          <ScatterChart margin={{ top: 6, right: 10, left: 0, bottom: 4 }}>
            <CartesianGrid stroke="var(--border)" vertical={false} />
            <XAxis type="number" dataKey="day" domain={[0, 365]} tickCount={7} tick={{ fontSize: 11 }} stroke="var(--text-3)" />
            <YAxis type="number" dataKey="y" domain={[-0.5, sensors.length - 0.5]} ticks={sensors.map((_, i) => i)} tickFormatter={(i) => sensors[i]} width={90} tick={{ fontSize: 11 }} stroke="var(--text-3)" />
            <ZAxis range={[30, 30]} />
            <Tooltip cursor={false} content={({ payload }) => payload?.length ? <div className="panel" style={{ padding: 6 }}>{`day ${payload[0].payload.day.toFixed(1)} · ${payload[0].payload.sensor} · ${payload[0].payload.kind}${payload[0].payload.r ? ` · ${fmtNum(payload[0].payload.r)} kg/h` : ""}${payload[0].payload.scene != null ? ` · in the scene framed on facility #${payload[0].payload.scene}` : ""}`}</div> : null} />
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
