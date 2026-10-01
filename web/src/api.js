// Thin API client. In dev, Vite proxies /api to uvicorn on :8000; in prod the API serves web/dist itself.
const J = (r) => {
  if (!r.ok) return r.text().then((t) => { throw new Error(`${r.status} ${t.slice(0, 300)}`); });
  return r.json();
};
export const api = {
  health: () => fetch("/api/health").then(J),
  sensors: () => fetch("/api/sensors").then(J),
  runs: () => fetch("/api/runs").then(J),
  estimate: (body) => fetch("/api/estimate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(J),
  startRun: (body) => fetch("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(J),
  job: (id) => fetch(`/api/jobs/${id}`).then(J),
  summary: (runId, kpi) => fetch(`/api/run/${runId}/summary?kpi=${kpi}`).then(J),
  facilities: (runId, kpi) => fetch(`/api/run/${runId}/facilities?kpi=${kpi}`).then(J),
  facility: (runId, fid) => fetch(`/api/run/${runId}/facility/${fid}`).then(J),
  budget: (runId, fid) => fetch(`/api/run/${runId}/facility/${fid}/budget`, { method: "POST" }).then(J),
  tornado: (runId, body) => fetch(`/api/run/${runId}/tornado`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(J),
  attribution: (runId) => fetch(`/api/run/${runId}/attribution`).then(J),
  references: () => fetch("/api/references").then(J),
  quickstart: () => fetch("/api/quickstart").then(J),
  validation: () => fetch("/api/validation").then(J),
  runValidation: (quick, allow) => fetch(`/api/validation/run?quick=${quick}&allow_placeholder=${allow}`, { method: "POST" }).then(J),
  optimize: (body) => fetch("/api/optimize", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(J),
  gap: (body) => fetch("/api/gap-analysis", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(J),
};

// Poll a job until done; calls onProgress with the job each tick.
export async function waitForJob(jobId, onProgress, intervalMs = 2000) {
  for (;;) {
    const j = await api.job(jobId);
    onProgress && onProgress(j);
    if (j.status === "done") return j.result;
    if (j.status === "error") throw new Error(j.error || "job failed");
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

// Three-state classification at the user's (B, w_max), mirroring mrvsim.score.metrics.classify (PRD 5.3-5.4).
export function classify(p, B, wMax) {
  if (!Number.isFinite(p.p50)) return "unscored";
  const w = p.p50 > 0 ? (p.p95 - p.p05) / (2 * p.p50) : Infinity;
  if (p.p10 > B) return "fails";
  if (p.p90 <= B && w <= wMax) return "certified";
  return "indeterminate";
}

export const fmtPct = (x, d = 2) => (Number.isFinite(x) ? `${(100 * x).toFixed(d)} %` : "–");
export const fmtNum = (x, d = 1) => (Number.isFinite(x) ? x.toLocaleString(undefined, { maximumFractionDigits: d }) : "–");
export const fmtUsd = (x) => (Number.isFinite(x) ? `$${Math.round(x).toLocaleString()}` : "–");
