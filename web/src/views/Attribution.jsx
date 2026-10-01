import React, { useEffect, useState } from "react";
import { api } from "../api.js";

// Every citation key behind the displayed run, resolved against REFERENCES.md; verify-status flagged (PRD F13).
export default function Attribution({ runId }) {
  const [a, setA] = useState(null); const [err, setErr] = useState(null);
  useEffect(() => { if (!runId) return; api.attribution(runId).then(setA).catch((e) => setErr(String(e))); }, [runId]);
  if (err) return <div className="panel flagged">{err}</div>;
  if (!a) return <div className="panel muted">{runId ? "Loading…" : "No run selected."}</div>;
  return (
    <div>
      <div className="panel"><h2>Provenance of this run</h2>
        <p>Priors: <b className={a.priors_provenance === "PLACEHOLDER" ? "flagged" : ""}>{a.priors_provenance}</b> · Strata weights: <b>{a.strata_provenance}</b> · {a.n_flagged} of {a.items.length} citations need verification.</p>
        <table className="grid"><thead><tr><th>sensor</th><th>tier</th><th>blocks not fitted to blind-test data</th></tr></thead>
          <tbody>{a.sensor_provenance.map((s) => <tr key={s.sensor}><td>{s.sensor}</td><td>{s.tier}</td><td className={s.unverified_blocks.length ? "flagged" : ""}>{s.unverified_blocks.join(", ") || "none"}</td></tr>)}</tbody></table>
      </div>
      <div className="panel"><h2>Citations used</h2>
        <table className="grid"><thead><tr><th>key</th><th>citation</th><th>DOI / URL</th><th>status</th></tr></thead>
          <tbody>{a.items.map((it) => <tr key={it.key}><td style={{ fontFamily: "var(--mono)", fontSize: 12 }}>{it.key}</td><td>{it.citation || <span className="flagged">not in REFERENCES.md</span>}</td><td style={{ fontSize: 12 }}>{it.doi_or_url}</td><td className={it.flag ? "flagged" : ""}>{it.status}</td></tr>)}</tbody></table>
      </div>
    </div>
  );
}
