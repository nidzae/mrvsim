import React, { useState } from "react";
import { api } from "../api.js";

// Gap-analysis chat (PRD F11): the API server holds the Anthropic credentials; nothing in the browser.
export default function GapAnalysis({ runId, onApply }) {
  const [msgs, setMsgs] = useState([{ role: "assistant", content: "Ask about the current run: why facilities are indeterminate or certified only on the prior, which error source dominates, or what the cheapest change to reach a target is. I can propose a sensor configuration you can apply with one click." }]);
  const [text, setText] = useState(""); const [busy, setBusy] = useState(false); const [err, setErr] = useState(null); const [proposal, setProposal] = useState(null);
  const send = async () => {
    if (!text.trim() || !runId) return;
    const next = [...msgs.filter((m) => m.role !== "assistant" || msgs.indexOf(m) > 0), { role: "user", content: text }];
    setMsgs([...msgs, { role: "user", content: text }]); setText(""); setBusy(true); setErr(null);
    try {
      const r = await api.gap({ run_id: runId, messages: next.map((m) => ({ role: m.role, content: m.content })) });
      setMsgs((m) => [...m, { role: "assistant", content: r.text }]);
      if (r.policy_yaml) setProposal(r.policy_yaml);
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };
  return (
    <div className="chat">
      <div className="msgs">
        {!runId && <p className="muted">Run a configuration first; the chat gets the run's numbers in context.</p>}
        {msgs.map((m, i) => <div key={i} className={`msg ${m.role}`}>{m.content}</div>)}
        {busy && <div className="msg assistant muted">thinking…</div>}
        {err && <div className="msg assistant flagged">{err}</div>}
        {proposal && <div className="panel"><b>Proposed configuration</b><pre className="yaml">{proposal}</pre><button className="primary" onClick={() => onApply(proposal)}>Apply</button> <span className="muted">loads it into Sensors; then press Run</span></div>}
      </div>
      <div className="composer"><textarea value={text} onChange={(e) => setText(e.target.value)} placeholder="Why is the Permian mostly grey?" onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); }} /><button className="primary" disabled={busy || !runId} onClick={send}>Send</button></div>
    </div>
  );
}
