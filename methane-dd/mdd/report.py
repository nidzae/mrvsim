"""One HTML report per run (SPEC section 8a): delivered intensity, go/no-go, segment intensities, per-asset contributions, sources."""

from __future__ import annotations

import html
from pathlib import Path

import numpy as np

from mdd.pipeline import RunResult

CSS = """body{font:14px/1.45 system-ui,sans-serif;margin:24px auto;max-width:1100px;color:#1d1d1b;padding:0 16px}
table{border-collapse:collapse;margin:8px 0 16px}td,th{border-bottom:1px solid #ddd;padding:4px 8px;text-align:left;font-size:13px}td.num{text-align:right}
.grade{display:inline-block;padding:4px 10px;border-radius:6px;color:#fff;font-weight:600}.Failed{background:#c0392b}.Flagged{background:#e67e22}.Screened{background:#7f8c8d}.Verified{background:#27ae60}
.muted{color:#666}.tiles{display:flex;gap:12px;flex-wrap:wrap}.tile{border:1px solid #ddd;border-radius:8px;padding:10px 14px;min-width:180px}.tile b{font-size:20px}"""


def _pct(x: float, d: int = 2) -> str:
    return "—" if x is None or not np.isfinite(x) else f"{x * 100:.{d}f} %"


def write_report(res: RunResult, path: Path | None = None) -> Path:
    c = res.scorecard; m = res.meta
    path = path or res.run_dir / "report.html"
    g = c.grade.split(",")[0]
    rows_seg = "".join(f"<tr><td>{html.escape(s)}</td><td class=num>{v['n_assets']}</td><td class=num>{_pct(v['p5'])}</td><td class=num>{_pct(v['p50'])}</td>"
                       f"<td class=num>{_pct(v['assured'])}</td><td class=num>{_pct(v['measured_floor'])}</td><td class=num>{v['evidence_share'] * 100:.0f} %</td>"
                       f"<td><span class='grade {v['grade'].split(',')[0]}'>{html.escape(v['grade'])}</span></td></tr>" for s, v in c.segments.items())
    pa = {r["asset_id"]: r for r in res.per_asset}
    rows_assets = "".join(
        f"<tr><td>{html.escape(str(r.get('name', r['asset_id'])))}<div class=muted>{html.escape(r['asset_id'])}</div></td><td>{html.escape(r['segment'])}</td>"
        f"<td class=num>{pa.get(r['asset_id'], {}).get('n_looks', 0)}</td><td class=num>{pa.get(r['asset_id'], {}).get('n_detections', 0)}</td>"
        f"<td class=num>{r['p50_kg_yr'] / 1000:.1f}</td><td class=num>{r['p95_kg_yr'] / 1000:.1f}</td><td class=num>{r['floor_p5_kg_yr'] / 1000:.2f}</td>"
        f"<td class=num>{r['share_of_assured'] * 100:.1f} %</td><td class=num>{r['measured_share'] * 100:.0f} %</td><td>{html.escape(r['sub_basis'])}</td>"
        f"<td class=muted>{html.escape(str(pa.get(r['asset_id'], {}).get('throughput_source', '')))}</td></tr>" for r in c.contributions)
    u = c.units
    body = f"""<!doctype html><html><head><meta charset="utf-8"><title>Methane due diligence: {html.escape(res.run_dir.name)}</title><style>{CSS}</style></head><body>
<h1>Supply-chain methane due diligence</h1>
<p class=muted>Run {html.escape(res.run_dir.name)} · generated {html.escape(m['generated_utc'])} · window {html.escape(m['window'][0][:10])} to {html.escape(m['window'][1][:10])} · area {html.escape(res.area.source)}</p>
<p><span class="grade {g}">{html.escape(c.grade)}</span> &nbsp; <b>Recommendation:</b> {html.escape(c.recommendation)}</p>
<div class=tiles>
<div class=tile>Assured delivered intensity (p95)<br><b>{_pct(c.assured_intensity)}</b><div class=muted>{u['kg_ch4_per_mmbtu']:.3f} kg CH4/MMBtu · {u['g_co2e_per_mj']:.2f} g CO2e/MJ (GWP100 29.8)</div></div>
<div class=tile>Target<br><b>{_pct(c.target)}</b></div>
<div class=tile>Delivered intensity p5 / p50<br><b>{_pct(c.delivered['p5'])} / {_pct(c.delivered['p50'])}</b></div>
<div class=tile>Measured floor (p5 of detected sources)<br><b>{_pct(c.measured_floor)}</b><div class=muted>fails if above target</div></div>
<div class=tile>Evidence share<br><b>{c.evidence_share * 100:.0f} %</b><div class=muted>of the assured intensity that is measured</div></div>
<div class=tile>Data<br><b>{m['n_assets']} assets</b><div class=muted>{m['n_looks']} looks · {m['n_attributed']} attributed plume rows · {m['n_scenes']} scenes · {m['n_plumes']} plumes in the box</div></div>
</div>
<h2>Segments</h2>
<table><tr><th>segment</th><th>assets</th><th>p5</th><th>p50</th><th>assured (p95)</th><th>measured floor</th><th>evidence</th><th>grade</th></tr>{rows_seg}</table>
<h2>Assets, by contribution to the assured intensity</h2>
<table><tr><th>asset</th><th>segment</th><th>clear looks</th><th>detections</th><th>p50 t/yr</th><th>p95 t/yr</th><th>floor p5 t/yr</th><th>share of assured</th><th>measured</th><th>sub-threshold basis</th><th>throughput source</th></tr>{rows_assets}</table>
<h2>Method and limits</h2>
<ul>
<li>Duty factor with imperfect detection (SPEC 6a), prior: {html.escape(m['prior'])}; the per-asset table in <code>per_asset.parquet</code> also gives the uniform-prior median.</li>
<li>Sub-threshold emissions: {html.escape(m['sub_threshold'])}. A literature prior is an assumption, not a measurement; it caps the grade at "Screened, unverified".</li>
<li>Wind: {html.escape(m['wind'])}. Scene footprints are bounding boxes of Carbon Mapper scenes; exact polygons and cloud masks are a later step.</li>
<li>Customer shares: {html.escape('; '.join(m.get('share_notes', [])) or 'as supplied')}.</li>
<li>Throughputs: see the last column; every value names its source and period (SPEC 4b).</li>
</ul>
<h2>Sources</h2>
<ul><li>Carbon Mapper public API, plumes and scenes, retrieved {html.escape(', '.join(m['sources']['carbonmapper']['retrieved']) or 'from cache')} [carbonmapper-api]</li>
<li>OGIM v3.0 for assets and 2022 production [ogim]</li><li>Sensor detection curves: config/sensors.yaml with citations</li></ul>
</body></html>"""
    path.write_text(body, encoding="utf-8")
    return path
