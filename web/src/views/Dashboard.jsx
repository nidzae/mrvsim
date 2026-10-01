import React, { useEffect, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ScatterChart, Scatter, CartesianGrid, ReferenceLine, Cell } from "recharts";
import { api, waitForJob, fmtNum, fmtPct, fmtUsd } from "../api.js";

function Tile({ label, value, sub, flag }) {
  return <div className={`tile${flag ? " flag" : ""}`}><div className="label">{label}</div><div className="value">{value}</div>{sub && <div className="sub">{sub}</div>}</div>;
}

function SliceTable({ title, rows }) {
  if (!rows?.length) return null;
  return (
    <div className="panel"><h2>{title}</h2>
      <table className="grid"><thead><tr><th>group</th><th className="num">n</th><th className="num">certified</th><th className="num">fails</th><th className="num">indeterminate</th><th className="num">cert. (thr-wtd)</th><th className="num">precise</th><th className="num">prior-only</th><th className="num">median w</th><th className="num">coverage</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.group}><td>{r.group}</td><td className="num">{r.n}</td><td className="num">{fmtPct(r.certified_share, 0)}</td><td className="num">{fmtPct(r.fails_share, 0)}</td><td className="num">{fmtPct(r.indeterminate_share, 0)}</td><td className="num">{fmtPct(r.certified_share_throughput, 0)}</td><td className="num">{fmtPct(r.precise_share, 0)}</td><td className="num">{fmtPct(r.certified_prior_only_share, 0)}</td><td className="num">{fmtNum(r.width_median, 2)}</td><td className="num">{fmtPct(r.coverage, 0)}</td></tr>)}</tbody></table>
      <p className="muted" style={{ marginTop: 6 }}>certified = p95 ≤ bar; fails = p5 &gt; bar; indeterminate = interval straddles the bar. precise = w ≤ w_max (attribute); prior-only = certified but the data barely narrowed the prior.</p>
    </div>
  );
}

// Headline metrics, tornado, Pareto frontier, slices (PRD F10).
export default function Dashboard({ runId, kpi, runSettings, pareto, onRerunFull }) {
  const [s, setS] = useState(null); const [err, setErr] = useState(null); const [busy, setBusy] = useState(false); const [prog, setProg] = useState(null);
  useEffect(() => { if (!runId) return; setS(null); api.summary(runId, kpi).then(setS).catch((e) => setErr(String(e))); }, [runId, kpi]);
  const runTornado = async () => {
    setBusy(true); setErr(null);
    try { const j = await api.tornado(runId, runSettings); await waitForJob(j.job_id, (jj) => setProg(jj.progress)); setS(await api.summary(runId, kpi)); } catch (e) { setErr(String(e)); } finally { setBusy(false); setProg(null); }
  };
  if (err) return <div className="panel flagged">{err}</div>;
  if (!s) return <div className="panel muted">{runId ? "Loading summary…" : "No run selected. Use Sensors → Run."}</div>;
  const k = s.summary[kpi]; const cal = k.calibration; const calOk = cal.mean >= 0.85 && cal.mean <= 0.95;
  const rows = s.tornado?.rows || [];
  const paretoPts = pareto?.pareto?.map((t) => ({ cost: t.cost_usd, width: t.width, name: `trial ${t.number}` })) || [];
  const allPts = pareto?.all_trials?.filter((t) => Number.isFinite(t.cost_usd)).map((t) => ({ cost: t.cost_usd, width: t.width, name: `trial ${t.number}`, feasible: t.feasible })) || [];
  return (
    <div>
      <div className="tiles">
        <Tile label="Certified share (facilities)" value={fmtPct(k.certified_share_facilities.mean, 1)} sub={`± ${fmtPct(k.certified_share_facilities.se, 1)} MC SE · precise ${fmtPct(k.certified_precise_share_facilities?.mean, 0)} · prior-only ${fmtPct(k.certified_prior_only_share_facilities?.mean, 0)}`} />
        <Tile label="Certified share (throughput)" value={fmtPct(k.certified_share_weighted_throughput.mean, 1)} sub={`stratum-weighted · precise ${fmtPct(k.certified_precise_share_weighted_throughput?.mean, 0)} · prior-only ${fmtPct(k.certified_prior_only_share_weighted_throughput?.mean, 0)}`} />
        <Tile label="Calibration κ" value={fmtNum(cal.mean, 3)} sub={calOk ? "within 0.85–0.95" : "OUTSIDE 0.85–0.95: intervals not trustworthy"} flag={!calOk} />
        <Tile label="Median interval half-width w" value={fmtNum(k.width_median.mean, 2)} sub={`bias ${fmtPct(k.bias_median.mean, 1)}`} />
        <Tile label="Completeness C" value={fmtPct(s.summary.completeness.mean, 1)} sub="sources > 10 kg/h, Jacob 2022" />
        <Tile label="Cost / tonne detected" value={fmtUsd(s.summary.cost.cost_per_tonne_detected_usd.mean)} sub={`total ${fmtUsd(s.summary.cost.cost_total_usd.mean)} (sample)`} />
        <Tile label="Cost / certified MMBtu" value={fmtUsd(s.summary.cost.cost_per_certified_mmbtu_usd?.mean)} sub={`${s.summary.n_replications} replications`} />
      </div>
      <div className="row" style={{ marginTop: 12 }}>
        <div className="panel" style={{ flex: 1, minWidth: 360 }}>
          <h2>Tornado: one-at-a-time sensor changes (Δ median w)</h2>
          {!rows.length && <div><p className="muted">Each bar reruns the pipeline with one sensor change at the current interactive settings.</p>
            <button className="primary" disabled={busy} onClick={runTornado}>{busy ? `computing ${prog?.stage || ""} (${prog?.done ?? 0}/${prog?.total ?? "?"})` : "Compute tornado"}</button></div>}
          {rows.length > 0 && (
            <div style={{ height: 30 + 26 * rows.length }}>
              <ResponsiveContainer>
                <BarChart data={rows} layout="vertical" margin={{ left: 10, right: 30 }}>
                  <CartesianGrid stroke="var(--border)" horizontal={false} />
                  <XAxis type="number" tick={{ fontSize: 11 }} stroke="var(--text-3)" tickFormatter={(v) => v.toFixed(2)} />
                  <YAxis type="category" dataKey="change" width={170} tick={{ fontSize: 11 }} stroke="var(--text-3)" />
                  <ReferenceLine x={0} stroke="var(--text-2)" />
                  <Tooltip formatter={(v, n, p) => [`${v.toFixed(3)} (w ${p.payload.width_median.toFixed(2)}, Δcost ${fmtUsd(p.payload.delta_cost)})`, "Δ width"]} />
                  <Bar dataKey="delta_width" radius={4}>{rows.map((r, i) => <Cell key={i} fill={r.delta_width <= 0 ? "#2a78d6" : "#eb6834"} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
        <div className="panel" style={{ flex: 1, minWidth: 360 }}>
          <h2>Pareto frontier: cost vs. precision at the current bar</h2>
          {!allPts.length && <p className="muted">Run the optimizer (Optimize tab) to populate the frontier.</p>}
          {allPts.length > 0 && (
            <div style={{ height: 260 }}>
              <ResponsiveContainer>
                <ScatterChart margin={{ left: 10, right: 20, top: 10, bottom: 10 }}>
                  <CartesianGrid stroke="var(--border)" />
                  <XAxis type="number" dataKey="cost" name="cost" tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} tick={{ fontSize: 11 }} stroke="var(--text-3)" label={{ value: "annual cost (sample)", position: "insideBottom", offset: -4, fontSize: 11 }} />
                  <YAxis type="number" dataKey="width" name="w" tick={{ fontSize: 11 }} stroke="var(--text-3)" label={{ value: "median w", angle: -90, position: "insideLeft", fontSize: 11 }} />
                  <Tooltip formatter={(v, n) => (n === "cost" ? fmtUsd(v) : v.toFixed(3))} />
                  <Scatter name="infeasible / dominated" data={allPts.filter((p) => !paretoPts.some((q) => q.cost === p.cost && q.width === p.width))} fill="#c3c2b7" />
                  <Scatter name="Pareto set" data={paretoPts} fill="#2a78d6" line={{ stroke: "#2a78d6" }} />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          )}
          <div className="legend"><span><span className="dot" style={{ background: "#2a78d6" }} />Pareto set</span><span><span className="dot" style={{ background: "#c3c2b7" }} />other trials</span></div>
        </div>
      </div>
      <SliceTable title="By basin" rows={s.slices.basin} />
      <SliceTable title="By facility type" rows={s.slices.facility_type} />
      <SliceTable title="By true rate bin" rows={s.slices.rate_bin} />
      {s.config?.estimator?.mode !== "full" && onRerunFull && (
        <div className="panel"><b>Looks good?</b> <span className="muted">This was a {s.config?.estimator?.mode || "custom"} run ({s.config?.estimator?.facilities_per_stratum ?? "all"} facilities per stratum, {s.config?.estimator?.n_draws} draws, R = {s.summary.n_replications}).</span>{" "}
          <button className="primary" onClick={() => onRerunFull(s.config)}>Re-run this mix at full resolution</button></div>)}
      <div className="panel muted">Run {s.run_id} · mode {s.config?.estimator?.mode || "custom"} · seed {s.manifest.seed} · git {s.manifest.git?.sha?.slice(0, 8) || "n/a"} · {fmtNum(s.manifest.elapsed_s, 0)} s · priors {s.summary.meta?.priors_provenance}</div>
    </div>
  );
}
