# 2026-10-01 — Certification rule: decision at 95 %, precision and evidence as attributes

## What changed

Nidhi's question: the facility in the drill-down had its whole interval (4.3–12.8 % intensity) under a 15 % bar but was grey because the relative half-width w = 0.58 exceeded w_max = 0.30. For a bar-compliance use case that is the wrong answer. Decision (DECISION_LOG 2026-10-01 "certification is the compliance decision at 95 %"):

- **Rule.** certified ⇔ p95 ≤ B; fails ⇔ p5 > B; indeterminate ⇔ the interval straddles B. Confidence level 95 % (was 90 %). Width no longer vetoes.
- **Precision** is a grade: precise iff w ≤ w_max. Shown as a dark ring on the map and a badge in the drill-down. w stays the optimizer objective and Pareto axis.
- **Evidence** (new PRD §5.4a): per facility, the posterior ln(p95/p5) as a fraction of the prior's on the same draws, plus counts of usable snapshots, survey visits and CMS hours. Certifications with ratio > 0.9 or no usable observation are flagged **prior-only** (faded on the map, red badge in the drill-down, counted in the dashboard tiles and slice tables).
- **P(K ≤ B | data)** is stated directly in the drill-down, interpolated on a persisted 101-point posterior quantile grid.

## Code

- `mrvsim/estimate/fast.py`: `QGRID`, `log_width_ratio`, `prior_summary` (prior percentiles on common random numbers, same percentile convention as the posterior so a no-data run gives ratio exactly 1), `PosteriorSummary.quantiles/prob_below/prior_interval/evidence_ratio`; `run_fast_estimator` fills them. `inputs.evidence_counts`.
- `mrvsim/score/metrics.py`: `classify` (decision only), `precise`, `prior_only`, `Bar.prior_only_ratio`; new share keys `precise_share_facilities`, `certified_precise_share_*`, `certified_prior_only_share_*`. `aggregate.state_counts` carries `precise`, `certified_precise`, `certified_prior_only`.
- `mrvsim/pipeline.py` persists `posterior_*_quantiles`, `prior_*_pcts`, `evidence_counts`. `mrvsim/api/store.py` loads them when present (older runs fall back: no probability grid, no evidence, never flagged), adds `q`, `prior_ratio`, `prior_only`, `n_obs`, `cms_h` to the map GeoJSON, `prior`, `quantiles`, `evidence`, `w_max` to the facility detail, `precise_share` and `certified_prior_only_share` to slices.
- `web/src`: `classify(p, B)`, `precision`, `probBelow`, `evidence` helpers; map ring/fade channels and legend; drill-down probability headline, decision sentence, prior row and band, precision and evidence lines; dashboard sub-lines and slice columns; top-bar label "precision w_max".
- `configs/default.yaml`: `scoring.prior_only_ratio: 0.9`.

## Effect on the default quick run (seed 20260930, bar 0.2 % intensity, w_max 0.3)

| | old rule (p90 ≤ B and w ≤ w_max) | new rule (p95 ≤ B) |
|---|---|---|
| certified share, facilities | 0.0 % | 26.8 % |
| certified share, throughput-weighted | 0.0 % | 30.5 % |
| indeterminate | 53 % | 45 % |
| fails | 47 % | 28 % |
| certified and precise | – | 8.8 % |
| certified and prior-only | – | 1.4 % |
| calibration κ | 0.901 | 0.896 |

Under the old rule nothing certified at 0.2 % because well-pad intervals are wide (w ≈ 3.5); under the new rule a quarter of facilities certify because their whole interval sits under the bar, and the precision grade now says separately that most of those numbers are imprecise. Fails dropped because p5 > B is a stricter failure statement than p10 > B.

An evidence note: TROPOMI-only facilities have hundreds of usable snapshots yet an evidence ratio near 1, because a 6 t/h detection threshold says nothing about a 0.3 kg/h site. The flag therefore says "the data did not narrow the prior", not "nobody looked", and the drill-down wording reflects that.

## Tests

`tests/unit` 133 passed. New: decision-only exhaustive classification incl. the wide-but-below-bar case; `precise`; `prior_only` with ratio and zero-evidence paths and the older-run fallback; `prob_below` interpolation and monotonicity; evidence ratio < 0.5 with 20 aircraft passes and exactly 1 with no observations; persisted arrays; API GeoJSON and facility-detail fields.

## Docs

PRD §5.3 (amended, 95 %), §5.4 (superseded as a condition), §5.4a (new), §11 row; TDD §6.7 note, §7 rows, §8.3 note, §13 row; QUICKSTART colours, drill-down, conventions; DECISION_LOG entry superseding three 2026-09-30 items.

## Open

- Validation V4 compares the certified-share stability across seeds; its target is unchanged, its meaning is now the decision-only share. Re-run V1–V7 before quoting them.
- The map's "wide" ring uses the top-bar w_max; the optimizer's w_max is set on its own panel. Both default to 0.3.

## Addendum (later the same day): sensor assignment visible; regional flight campaigns

Nidhi asked why, of three neighbouring sites, only one had GHGSat. Answer (now shown in the drill-down under "Monitoring at this facility"): sensors are assigned per facility by coverage share and targeting with no geography; `throughput` targeting is a top-k cutoff by marketed gas. The green site ranked 290 of 1,890 and so had GHGSat (top 30 %) and a continuous monitor (top 20 %); its neighbours ranked 743 and 989. Added `scheduling: campaign` with `campaign_days` for campaign/survey sensors so each basin is flown in one window per slice of the year (DECISION_LOG "Regional flight campaigns"); the UI default policy uses it for Bridger. The "throughput-weighted" label is corrected everywhere. Runs scored before the quantile grid now show "< 5 %" / "> 95 %" bounds instead of "–" and a note to re-run. Tests: `tests/unit/test_deployment.py` (window length, per-facility visit counts, fallback, policy round-trip).
