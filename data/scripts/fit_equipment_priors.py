#!/usr/bin/env python
"""Fit per-well equipment emission parameters by well class and productivity bin [rutherford2021].

Reads the 100 uncertainty realizations of Rutherford et al.'s component-based model
(``5_Processing_Results/5a_OPGEE_Processing/Set 21/Equip{k}out.csv``): each row is one simulated well
with its tranche (well class x productivity bin), its sampling weight, and its annual-average methane
emission in kg/day for each of 16 equipment categories (column map in ``mat_extend.m``). Writes
``configs/priors/equipment_cells_<date>.yaml`` and ``data/fitted/equipment_priors_provenance.json``.

Method (all recorded in the output):

- **Classes and bins** follow ``tranche_data.m``: dry gas (no oil), gas with oil (gas-to-oil ratio above
  100 Mscf/bbl), oil with gas (ratio below 100), oil only (no gas). Gas classes and oil-with-gas are binned
  by gas per well per day (<1, 1-5, 5-10, 10-20, 20-50, 50-100, 100-500, 500-1,000, 1,000-10,000,
  >10,000 Mscf/d); oil-only wells by four bins with edges 0.5, 1 and 10. The oil-only bin variable is read
  here as oil in bbl/d per well, which is an interpretation of the script, TODO(verify) [rutherford2021].
  The three tranches per gas bin (liquids-unloading variants) are pooled with their sampling weights.
- **Steady categories**: wellhead, header, heater, separator, meter, tank leaks, reciprocating compressor,
  dehydrator, chemical injection pump, pneumatic controller. **Episodic categories**: tank vents, liquids
  unloading, tank flashing. **Excluded from routine emissions** (reported, not modelled): completions,
  workovers, flare methane.
- Per cell and group: the expected number of emitting categories per well, and a lognormal for the
  emission of one emitting category fitted to its **mean and median** (mu = ln median,
  sigma = sqrt(2 ln(mean / median))). Matching the mean keeps emitted mass exact; a maximum-likelihood
  fit on logs would not, because the data are bounded resamples of measured leaks with a log-sd near 3.
  Quantile errors of that fit (p75, p90, p95, p99) are recorded per cell as goodness of fit.
- Each quantity is computed per realization; the file stores the mean over realizations and the
  standard deviation across them (parameter uncertainty).

Run:

    .venv/bin/python data/scripts/fit_equipment_priors.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.stats import norm

REPO = Path(__file__).resolve().parents[2]
SET = (REPO / "data" / "raw" / "rutherford2021" / "JSRuthe-O-G_Methane_Supporting_Code-ed1b142" / "5_Processing_Results"
       / "5a_OPGEE_Processing" / "Set 21")
N_REALIZATIONS = 100
# Columns of Equip{k}out.csv (mat_extend.m): flag, tranche id, sampling weight (actual wells per sampled well),
# productivity, a scenario column, then the 16 equipment categories in kg CH4 per well per day. [rutherford2021]
CATEGORIES = ("wells", "header", "heater", "separator", "meter", "tank_leaks", "tank_vents", "recip_compressor", "dehydrator",
              "chemical_injection_pump", "pneumatic_controller", "liquids_unloading", "completions", "workovers", "tank_flashing", "flare_methane")
STEADY = ("wells", "header", "heater", "separator", "meter", "tank_leaks", "recip_compressor", "dehydrator", "chemical_injection_pump", "pneumatic_controller")
EPISODIC = ("tank_vents", "liquids_unloading", "tank_flashing")
EXCLUDED = ("completions", "workovers", "flare_methane")
CLASSES = ("drygas", "gaswoil", "oilwgas", "oilonly")
GAS_BIN_EDGES_MSCF_D = (1.0, 5.0, 10.0, 20.0, 50.0, 100.0, 500.0, 1000.0, 10000.0)      # tranche_data.m [rutherford2021]
OIL_BIN_EDGES_BBL_D = (0.5, 1.0, 10.0)                                                   # interpretation, TODO(verify)
QUANTILES = (0.75, 0.90, 0.95, 0.99)


def _class_and_bin(tranche: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    cls = np.select([tranche < 230, tranche < 260, tranche < 270], [0, 1, 2], 3)
    b = np.where(tranche < 260, ((tranche - 200) % 30) // 3, np.where(tranche < 270, tranche - 260, tranche - 270))
    return cls, b


def _wquantile(x: np.ndarray, w: np.ndarray, q: float | np.ndarray) -> np.ndarray:
    o = np.argsort(x)
    cw = np.cumsum(w[o]) / w.sum()
    return np.interp(q, cw, x[o])


def _group_stats(e: np.ndarray, w: np.ndarray) -> dict[str, float]:
    """``e``: (n_wells, n_categories) kg/h. Stats of one emitting category, and emitting categories per well."""
    on = e > 0
    k = float((on.sum(axis=1) * w).sum() / w.sum())
    x, wx = e[on], np.broadcast_to(w[:, None], e.shape)[on]
    if x.size < 20:
        return {"k": k, "mean": float("nan"), "median": float("nan"), "sigma": float("nan"), **{f"qerr_p{int(q * 100)}": float("nan") for q in QUANTILES}}
    mean = float((x * wx).sum() / wx.sum())
    median = float(_wquantile(x, wx, 0.5))
    sigma = float(np.sqrt(2.0 * np.log(max(mean / median, 1.0 + 1e-9))))
    out = {"k": k, "mean": mean, "median": median, "sigma": sigma}
    for q in QUANTILES:        # ln(fitted quantile / empirical quantile): goodness of fit of the mean-median lognormal
        out[f"qerr_p{int(q * 100)}"] = float(np.log(median) + sigma * norm.ppf(q) - np.log(_wquantile(x, wx, q)))
    return out


def main() -> int:
    if not SET.exists():
        print(f"missing {SET}", file=sys.stderr)
        return 1
    per_real: dict[tuple[int, int], list[dict[str, float]]] = {}
    national_tg: list[dict[str, float]] = []
    for r in range(1, N_REALIZATIONS + 1):
        d = pd.read_csv(SET / f"Equip{r}out.csv", header=None).to_numpy(float)
        cls, b = _class_and_bin(d[:, 1].astype(int))
        w = d[:, 2]
        e = d[:, 5:21] / 24.0                                   # kg/day -> kg/h
        col = {c: i for i, c in enumerate(CATEGORIES)}
        national_tg.append({c: float((e[:, col[c]] * w).sum() * 8760 / 1e9) for c in CATEGORIES} | {"wells_count": float(w.sum())})
        for key in sorted(set(zip(cls.tolist(), b.tolist()))):
            m = (cls == key[0]) & (b == key[1])
            s = _group_stats(e[m][:, [col[c] for c in STEADY]], w[m])
            p = _group_stats(e[m][:, [col[c] for c in EPISODIC]], w[m])
            row = {f"steady_{k}": v for k, v in s.items()} | {f"episodic_{k}": v for k, v in p.items()}
            row["excluded_mean"] = float((e[m][:, [col[c] for c in EXCLUDED]].sum(axis=1) * w[m]).sum() / w[m].sum())
            row["wells"] = float(w[m].sum()); row["rows"] = float(m.sum())
            per_real.setdefault(key, []).append(row)

    cells: dict[str, list[dict]] = {c: [] for c in CLASSES}
    for (ci, bi), rows in sorted(per_real.items()):
        df = pd.DataFrame(rows)
        mean, sd = df.mean(), df.std(ddof=1)
        # Parameters from the realization-mean of the mean and median (so the cell mean is the realization-mean mass).
        cell = {"bin": int(bi), "wells_represented": float(mean["wells"]), "rows_per_realization": float(mean["rows"]),
                "excluded_mean_kg_h": float(mean["excluded_mean"])}
        for g in ("steady", "episodic"):
            m_, med = float(mean[f"{g}_mean"]), float(mean[f"{g}_median"])
            cell[g] = {"emitters_per_well": float(mean[f"{g}_k"]), "mean_kg_h": m_, "median_kg_h": med,
                       "sigma": float(np.sqrt(2.0 * np.log(max(m_ / med, 1.0 + 1e-9)))) if np.isfinite(m_) else float("nan"),
                       "sd_across_realizations": {"emitters_per_well": float(sd[f"{g}_k"]), "mean_kg_h": float(sd[f"{g}_mean"]), "median_kg_h": float(sd[f"{g}_median"])},
                       "ln_quantile_error": {f"p{int(q * 100)}": float(mean[f"{g}_qerr_p{int(q * 100)}"]) for q in QUANTILES}}
        cells[CLASSES[ci]].append(cell)

    nat = pd.DataFrame(national_tg).mean()
    stamp = dt.datetime.now(dt.timezone.utc)
    out = {
        "provenance": "FITTED", "version": stamp.strftime("%Y-%m-%d"), "citation_keys": ["rutherford2021"],
        "units": "kg CH4 per hour, annual average, per emitting equipment category on one well",
        "classes": {"drygas": "no oil", "gaswoil": "gas-to-oil ratio > 100 Mscf/bbl", "oilwgas": "gas-to-oil ratio < 100 Mscf/bbl", "oilonly": "no gas"},
        "gas_bin_edges_mscf_per_well_day": list(GAS_BIN_EDGES_MSCF_D), "oil_bin_edges_bbl_per_well_day": list(OIL_BIN_EDGES_BBL_D),
        "gor_cutoff_mscf_per_bbl": 100.0,
        "steady_categories": list(STEADY), "episodic_categories": list(EPISODIC), "excluded_categories": list(EXCLUDED),
        "cells": cells,
    }
    path = REPO / "configs" / "priors" / f"equipment_cells_{out['version']}.yaml"
    header = ("# Per-well equipment emission parameters by well class and productivity bin (provenance FITTED).\n"
              "# Produced by data/scripts/fit_equipment_priors.py from the component-based model of [rutherford2021]\n"
              "# (100 uncertainty realizations). Method and caveats in the script docstring; DECISION_LOG 2026-10-03.\n")
    path.write_text(header + yaml.safe_dump(out, sort_keys=False, width=140), encoding="utf-8")
    prov = {"fitted_utc": stamp.isoformat(timespec="seconds"), "script": "data/scripts/fit_equipment_priors.py", "source": str(SET.relative_to(REPO)),
            "n_realizations": N_REALIZATIONS, "method": __doc__.strip(),
            "national_tg_per_year_by_category": {c: float(nat[c]) for c in CATEGORIES}, "national_wells": float(nat["wells_count"]),
            "national_tg_per_year_modelled": float(sum(nat[c] for c in STEADY + EPISODIC)), "national_tg_per_year_excluded": float(sum(nat[c] for c in EXCLUDED)),
            "output": str(path.relative_to(REPO))}
    (REPO / "data" / "fitted" / "equipment_priors_provenance.json").write_text(json.dumps(prov, indent=2) + "\n", encoding="utf-8")
    for c in CLASSES:
        for cell in cells[c]:
            s, p = cell["steady"], cell["episodic"]
            print(f"{c:8s} bin {cell['bin']}: steady {s['emitters_per_well']:.2f} x {s['mean_kg_h']:.3f} kg/h (sigma {s['sigma']:.2f}, p95 err {s['ln_quantile_error']['p95']:+.2f}); "
                  f"episodic {p['emitters_per_well']:.2f} x {p['mean_kg_h']:.3f} kg/h (sigma {p['sigma']:.2f}, p95 err {p['ln_quantile_error']['p95']:+.2f})")
    print(f"national: modelled {prov['national_tg_per_year_modelled']:.2f} Tg/yr, excluded {prov['national_tg_per_year_excluded']:.2f} Tg/yr, wells {prov['national_wells']:.0f}")
    print(f"written {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
