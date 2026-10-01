import React, { useEffect, useState } from "react";
import { api, waitForJob } from "../api.js";

// V1-V7 pass/fail with the compared values (PRD F14).
export default function Validation() {
  const [v, setV] = useState(null); const [busy, setBusy] = useState(false); const [err, setErr] = useState(null); const [allow, setAllow] = useState(false);
  const load = () => api.validation().then(setV).catch((e) => setErr(String(e)));
  useEffect(() => { load(); }, []);
  const run = async () => { setBusy(true); setErr(null); try { const j = await api.runValidation(true, allow); await waitForJob(j.job_id, null, 5000); await load(); } catch (e) { setErr(String(e)); } finally { setBusy(false); } };
  if (err) return <div className="panel flagged">{err}</div>;
  if (!v) return <div className="panel muted">Loading…</div>;
  const results = v.latest.results || [];
  const color = { pass: "certified", fail: "fails", skipped: "indeterminate", error: "fails" };
  return (
    <div>
      <div className="panel">
        <h2>Validation tests V1–V7 (TDD §9)</h2>
        {v.priors_placeholder && <p className="flagged">Priors are PLACEHOLDER: validation tests refuse to run as validation. A diagnostic run exercises the machinery but proves nothing about published data.</p>}
        <label className="muted"><input type="checkbox" checked={allow} onChange={(e) => setAllow(e.target.checked)} /> allow diagnostic run on placeholder priors</label>
        <div style={{ marginTop: 8 }}><button className="primary" disabled={busy} onClick={run}>{busy ? "Running (10–20 min quick mode)…" : "Run quick validation"}</button> <span className="muted">latest: {v.path || "none"}</span></div>
      </div>
      {results.map((r) => (
        <div className="panel" key={r.id}>
          <h2>{r.id} — {r.name} <span className={`badge ${color[r.status]}`}>{r.status}</span>{r.diagnostic_only && <span className="badge indeterminate" style={{ marginLeft: 6 }}>diagnostic only</span>}</h2>
          <div className="muted">Pass rule: {r.pass_rule} · citations: {r.citations.join(", ") || "internal"} · target status: {r.target_status || "–"}</div>
          {r.reason && <div className="muted" style={{ marginTop: 4 }}>{r.reason}</div>}
          <details><summary className="muted">compared values</summary><pre className="yaml">{JSON.stringify(r.compared, null, 1)}</pre></details>
        </div>
      ))}
      {!results.length && <div className="panel muted">No validation results yet.</div>}
    </div>
  );
}
