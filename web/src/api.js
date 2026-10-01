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

// Three-state classification at the user's bar B, mirroring mrvsim.score.metrics.classify
// (PRD 5.3-5.4 as amended; DECISION_LOG 2026-10-01): a compliance decision at 95 %. Width does not enter.
export function classify(p, B) {
  if (!Number.isFinite(p.p50)) return "unscored";
  if (p.p05 > B) return "fails";
  if (p.p95 <= B) return "certified";
  return "indeterminate";
}

// Relative half-width w = (p95 - p5) / (2 median) (PRD 5.4) and the precision grade at w_max.
export const halfWidth = (p) => (p.p50 > 0 ? (p.p95 - p.p05) / (2 * p.p50) : Infinity);
export const precision = (p, wMax) => (halfWidth(p) <= wMax ? "precise" : "wide");

// P(K <= B | data): linear interpolation on the posterior quantile grid q (percent 0..100 at equal steps),
// falling back to the five stored percentiles for runs scored before the grid existed.
export function probBelow(p, B) {
  const q = p.q && p.q.length > 2 ? p.q : p.quantiles;
  let xs, ps;
  if (q && q.length > 2) { xs = q; ps = q.map((_, i) => i / (q.length - 1)); }
  else { xs = [p.p05, p.p10, p.p50, p.p90, p.p95]; ps = [0.05, 0.10, 0.50, 0.90, 0.95]; }
  if (!xs.every(Number.isFinite)) return NaN;
  if (B < xs[0]) return q ? 0 : NaN;
  if (B >= xs[xs.length - 1]) return q ? 1 : NaN;
  for (let i = 1; i < xs.length; i++) {
    if (B < xs[i]) { const t = xs[i] > xs[i - 1] ? (B - xs[i - 1]) / (xs[i] - xs[i - 1]) : 1; return ps[i - 1] + t * (ps[i] - ps[i - 1]); }
  }
  return 1;
}

// Evidence (PRD 5.4a): posterior ln(p95/p05) as a fraction of the prior's, usable observation counts, prior-only flag.
export const evidence = (p) => ({ ratio: Number.isFinite(p.prior_ratio) ? p.prior_ratio : NaN, nObs: p.n_obs ?? null, cmsH: p.cms_h ?? null, priorOnly: p.prior_only === true });

export const fmtPct = (x, d = 2) => (Number.isFinite(x) ? `${(100 * x).toFixed(d)} %` : "–");
export const fmtNum = (x, d = 1) => (Number.isFinite(x) ? x.toLocaleString(undefined, { maximumFractionDigits: d }) : "–");
export const fmtUsd = (x) => (Number.isFinite(x) ? `$${Math.round(x).toLocaleString()}` : "–");
