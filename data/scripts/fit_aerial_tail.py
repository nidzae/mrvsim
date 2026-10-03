#!/usr/bin/env python
"""Fit the aerial-survey super-emitter tail per basin, per well [sherwin2024].

The equipment-based leak model (``fit_equipment_priors.py``) is a bottom-up estimate. Aerial surveys find
rare, very large emissions that component surveys under-count. Sherwin et al. 2024 combine the two per
campaign: bottom-up simulation below a transition point T, aerial measurements above it. This script
fits, per basin, the part above T and writes ``configs/priors/aerial_tail_<date>.yaml``.

From the production-site files of the correction release (``CDF_data/*_production_202508010_*.csv``, ten
batches each; one row per covered well site) and its tables (S8 well sites covered, S10 covered methane
production, S21 transition point):

- ``T``: the campaign's transition point (kg/h).
- ``alpha``: Pareto index of site emission above T, from the mean excess ratio m = E[q | q >= T] / T as
  alpha = m / (m - 1), so that an unbounded Pareto has the campaign's mean emission above T. Bounded
  below at ``ALPHA_MIN`` to keep simulated totals from being driven by single draws; where the bound
  binds the frequency is raised so the mass still matches (recorded).
- ``tail_loss_rate``: emissions from production sites at or above T, over the campaign's covered methane
  production. ``production_loss_rate``: all production-site emissions over the same denominator (a
  production-only comparison value for validation V2; the published Table S10 rates include midstream).

Per-well frequency in MRVSim's own site table [ogim]:

- A site is **eligible** if its methane production rate is at least T: a site cannot leak more gas than
  it produces. (The surveys also mostly covered high-producing wells: Table S6.)
- Every well of an eligible site has the same chance ``p_per_well`` of showing an emission above T in a
  snapshot (site frequency min(n_wells * p, FREQ_MAX)). p is solved so that the basin's expected tail
  mass, sum over eligible sites of frequency x T alpha / (alpha - 1), equals tail_loss_rate x the basin's
  methane production in the site table.
- Basins without a survey use the pooled values of the four surveyed basins (T and alpha weighted by
  covered production, pooled tail loss rate). This is an assumption, TODO(verify).

Run:

    .venv/bin/python data/scripts/fit_aerial_tail.py
"""

from __future__ import annotations

import datetime as dt
import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from mrvsim.population.sites import load_sites  # noqa: E402

BASE = REPO / "data" / "raw" / "sherwin2024" / "full"
TARGETS = REPO / "data" / "fitted" / "sherwin2024_targets.json"
# MRVSim basin -> production-site CDF prefix (the campaigns used for the validation targets). [sherwin2024]
CAMPAIGNS = {
    "permian": "J - Permian Fall 2021Permian basin_production",
    "appalachian": "B - NorthEast_2021Appalachian basin (eastern overthrust area)_production",
    "dj": "I - DJ Fall 2021Denver basin_production",
    "uinta": "F - GAO_2020Uinta basin_production",
}
ALPHA_MIN = 1.2
FREQ_MAX = 0.5


def campaign_stats(prefix: str, T: float) -> dict[str, float]:
    rows = []
    for f in sorted(glob.glob(str(BASE / "CDF_data" / f"{prefix}_202508010_*.csv"))):
        d = pd.read_csv(f)
        x = d["Emission magnitude [kgh]"].to_numpy(float)
        ok = np.isfinite(x) & (x > 0)
        x = x[ok]                                  # one row per covered well site
        above = x >= T
        rows.append({"n_sites": float(x.size), "total_kg_h": float(x.sum()), "tail_kg_h": float(x[above].sum()), "freq_above_T": float(above.mean()),
                     "mean_above_T": float(x[above].mean()), "max_kg_h": float(x.max())})
    if not rows:
        raise FileNotFoundError(f"no CDF files for {prefix}")
    out = pd.DataFrame(rows).mean().to_dict()
    out["n_batches"] = len(rows)
    return out


def solve_p(n_wells: np.ndarray, mean_tail_kg_h: float, target_kg_h: float) -> float:
    """p such that sum_i min(n_i p, FREQ_MAX) * mean_tail = target (bisection; monotone in p)."""
    f = lambda p: float((np.minimum(n_wells * p, FREQ_MAX) * mean_tail_kg_h).sum())  # noqa: E731
    lo, hi = 0.0, 1.0
    if f(hi) < target_kg_h:
        return float("nan")
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if f(mid) < target_kg_h else (lo, mid)
    return 0.5 * (lo + hi)


def main() -> int:
    tg = json.loads(TARGETS.read_text(encoding="utf-8"))["basins"]
    constants = yaml.safe_load((REPO / "configs" / "constants.yaml").read_text(encoding="utf-8"))
    ch4_kg_per_m3 = float(constants["rho_ch4_kg_per_m3"]) * float(constants["x_ch4_default"])
    sites = load_sites()
    if sites is None:
        print("missing data/fitted/sites.npz; run build_sites.py", file=sys.stderr)
        return 1
    site_basin = np.array([k.split("/")[0] for k in sites.cell_keys])[sites.cell_idx]
    ch4_kg_h = sites.gas_m3_yr * ch4_kg_per_m3 / 8760.0

    surveyed: dict[str, dict] = {}
    for basin, prefix in CAMPAIGNS.items():
        T = float(tg[basin]["transition_point_kg_h"])
        st = campaign_stats(prefix, T)
        covered_kg_h = float(tg[basin]["covered_production_t_h"]) * 1000.0
        m = st["mean_above_T"] / T
        surveyed[basin] = {"campaign": tg[basin]["campaign"], "transition_kg_h": T, "alpha_from_mean_excess": m / (m - 1.0), "alpha": max(m / (m - 1.0), ALPHA_MIN),
                           "tail_loss_rate": st["tail_kg_h"] / covered_kg_h, "production_loss_rate": st["total_kg_h"] / covered_kg_h,
                           "survey": {"well_sites": st["n_sites"], "site_frequency_above_T": st["freq_above_T"], "mean_above_T_kg_h": st["mean_above_T"],
                                      "max_kg_h": st["max_kg_h"], "covered_production_t_h": covered_kg_h / 1000.0, "n_batches": st["n_batches"]}}
    w = np.array([surveyed[b]["survey"]["covered_production_t_h"] for b in surveyed])
    pooled = {"campaign": "pooled over " + ", ".join(surveyed), "transition_kg_h": float(np.average([surveyed[b]["transition_kg_h"] for b in surveyed], weights=w)),
              "alpha": float(np.average([surveyed[b]["alpha"] for b in surveyed], weights=w)),
              "tail_loss_rate": float(sum(surveyed[b]["tail_loss_rate"] * x for b, x in zip(surveyed, w)) / w.sum()), "pooled": True}

    basins: dict[str, dict] = {}
    for basin in sorted(set(site_basin.tolist())):
        blk = dict(surveyed.get(basin, pooled))
        T, alpha = blk["transition_kg_h"], blk["alpha"]
        in_basin = site_basin == basin
        eligible = in_basin & (ch4_kg_h >= T)
        mean_tail = T * alpha / (alpha - 1.0)
        target = blk["tail_loss_rate"] * float(ch4_kg_h[in_basin].sum())
        p = solve_p(sites.n_wells[eligible].astype(float), mean_tail, target) if eligible.any() else float("nan")
        blk.update({"p_per_well": p, "eligible_sites": int(eligible.sum()), "eligible_wells": int(sites.n_wells[eligible].sum()), "basin_sites": int(in_basin.sum()),
                    "eligible_share_of_basin_gas": float(ch4_kg_h[eligible].sum() / ch4_kg_h[in_basin].sum()), "target_tail_kg_h": target,
                    "mean_tail_emission_kg_h": mean_tail})
        basins[basin] = blk

    stamp = dt.datetime.now(dt.timezone.utc)
    out = {"provenance": "FITTED", "version": stamp.strftime("%Y-%m-%d"), "citation_keys": ["sherwin2024", "ogim"], "alpha_min": ALPHA_MIN, "frequency_max": FREQ_MAX,
           "fitted_utc": stamp.isoformat(timespec="seconds"), "basins": basins}
    path = REPO / "configs" / "priors" / f"aerial_tail_{out['version']}.yaml"
    header = ("# Aerial-survey super-emitter tail per basin, per well of an eligible site (provenance FITTED).\n"
              "# Produced by data/scripts/fit_aerial_tail.py from the Sherwin et al. 2024 correction release [sherwin2024] and the\n"
              "# OGIM site table [ogim]. Method and caveats in the script docstring; DECISION_LOG 2026-10-03.\n")
    path.write_text(header + yaml.safe_dump(json.loads(json.dumps(out)), sort_keys=False, width=140), encoding="utf-8")
    for b, v in basins.items():
        print(f"{b:12s} T {v['transition_kg_h']:6.1f} alpha {v['alpha']:.2f} tail loss {v['tail_loss_rate'] * 100:.2f}% p/well {v['p_per_well']:.5f} "
              f"eligible sites {v['eligible_sites']:6d} of {v['basin_sites']:6d} ({v['eligible_share_of_basin_gas'] * 100:.0f}% of gas)"
              + (f" | production-only loss {v['production_loss_rate'] * 100:.2f}% freq>=T {v['survey']['site_frequency_above_T']:.4f} alpha raw {v['alpha_from_mean_excess']:.2f}" if b in surveyed else " | pooled"))
    print(f"written {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
