#!/usr/bin/env python
"""Extract per-basin targets from the Sherwin et al. 2024 correction release (Zenodo 10.5281/zenodo.17968370) [sherwin2024].

Writes data/fitted/sherwin2024_targets.json with, per MRVSim basin:
  * loss_rate: Table S10 methane loss rate [%] with 95 % CI and emissions [t/h] for the chosen campaign
  * min_detected_kg_h, transition_point_kg_h: Table S21 (campaign detection floor and the aerial/simulation transition)
  * survival: fraction of production sites emitting at or above each level (kg/h), averaged over the 10 CDF batches
    ("Fraction of sites emitting at or above this level" in the CDF_data files; aerial above the transition point,
    Rutherford BASE simulation below)
  * detected_quantiles_kg_h: quantiles of site emission among aerial-detected sites (from "Fraction of aerial detect sites ...")
Campaign per basin: the most recent production-focused survey in the release.
"""

from __future__ import annotations

import glob
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
BASE = REPO / "data" / "raw" / "sherwin2024" / "full"
OUT = REPO / "data" / "fitted" / "sherwin2024_targets.json"
LEVELS = [0.1, 0.3, 1, 3, 10, 30, 100, 300, 1000]
# MRVSim basin -> (CDF scenario prefix, Table S10/S21 row label)
CAMPAIGNS = {
    "permian": ("J - Permian Fall 2021Permian basin_production", "Permian_fall2021"),
    "appalachian": ("B - NorthEast_2021Appalachian basin (eastern overthrust area)_production", "Pennsylvania_2021"),
    "dj": ("I - DJ Fall 2021Denver basin_production", "Denver_fall2021"),
    "uinta": ("F - GAO_2020Uinta basin_production", "Uinta_2020"),
    "fort_worth": ("Kairos BarnettFort Worth basin_production", "Fort_Worth_2021"),
    "san_joaquin": ("K - CA Fall 2021San Joaquin basin_production", "San_Joaquin_fall2021"),
}


def parse_ci(cell: str) -> tuple[float, float, float] | None:
    m = re.match(r"\s*([\d.]+)\s*\[\s*([\d.]+)\s*,\s*([\d.]+)\s*\]", str(cell))
    return (float(m.group(1)), float(m.group(2)), float(m.group(3))) if m else None


def tables() -> tuple[dict, dict]:
    x = pd.ExcelFile(BASE / "Other input data" / "Key sim results correction 20250812.xlsx")
    df = x.parse("Tables for paper (correction)", header=None)
    s10, s21 = {}, {}
    mode = None
    for _, row in df.iterrows():
        a = str(row[0])
        if a.startswith("Table S21"):
            mode = "s21"; continue
        if a.startswith("Table S10"):
            mode = "s10"; continue
        if a.startswith("Table S"):          # any other table: stop collecting (labels repeat across tables)
            mode = None; continue
        if a in ("nan", "NaN") or pd.isna(row[0]):
            continue
        if mode == "s21" and pd.notna(row[1]) and pd.notna(row[2]):
            try:
                s21.setdefault(a, {"transition_point_kg_h": float(row[1]), "min_detected_kg_h": float(row[2]), "n_production_plumes": float(row[3])})
            except (TypeError, ValueError):
                pass
        if mode == "s10" and isinstance(row[1], str) and "[" in row[1]:
            ci_e, ci_l = parse_ci(row[1]), parse_ci(row[3])
            if ci_e is None or ci_l is None:
                continue
            (e, elo, ehi), (lr, lo, hi) = ci_e, ci_l
            try:
                cov = float(row[2])
            except (TypeError, ValueError):
                cov = float("nan")
            s10.setdefault(a, {"emissions_t_h": e, "emissions_ci_95": [elo, ehi], "covered_production_t_h": cov, "loss_rate_pct": lr, "loss_rate_ci_95_pct": [lo, hi]})
    return s10, s21


def survival_for(prefix: str) -> dict:
    files = sorted(glob.glob(str(BASE / "CDF_data" / (prefix + "_*.csv"))))
    S = []; D = []; quant = []
    for f in files:
        d = pd.read_csv(f)
        x = d["Emission magnitude [kgh]"].to_numpy(float)
        s = d["Fraction of sites emitting at or above this level"].to_numpy(float)
        ok = np.isfinite(x) & np.isfinite(s) & (x > 0)
        x, s = x[ok], s[ok]
        order = np.argsort(x); x, s = x[order], s[order]
        S.append(np.interp(np.log(LEVELS), np.log(x), s, left=s[0], right=0.0))
        if "Fraction of aerial detect sites at or above this level" in d:
            fa = d["Fraction of aerial detect sites at or above this level"].to_numpy(float)[ok][order]
            good = np.isfinite(fa)
            if good.sum() > 10:
                xs, fs = x[good], fa[good]
                # quantiles of detected-site emission: fs is a survival fraction, decreasing in x
                qs = {}
                for p in (25, 50, 75, 90):
                    target = 1 - p / 100.0
                    idx = np.argmin(np.abs(fs - target)); qs[f"p{p}"] = float(xs[idx])
                quant.append(qs)
    if not S:
        return {}
    S = np.mean(np.stack(S), axis=0)
    out = {"levels_kg_h": LEVELS, "fraction_at_or_above": [float(v) for v in S], "n_batches": len(files), "_x": x.tolist(), "_s": s.tolist()}
    return out


def detected_quantiles(surv: dict, floor_kg_h: float) -> dict:
    """Quantiles of site emission among sites at or above the campaign detection floor, from the survival curve of one batch."""
    x = np.asarray(surv.pop("_x")); s = np.asarray(surv.pop("_s"))
    above = x >= floor_kg_h
    if above.sum() < 5 or s[above][0] <= 0:
        return {}
    cond = s[above] / s[above][0]        # conditional survival given >= floor
    xs = x[above]
    return {f"p{p}": float(np.interp(1 - p / 100.0, cond[::-1], xs[::-1])) for p in (25, 50, 75, 90)}


def main() -> int:
    s10, s21 = tables()
    out = {"source": "Sherwin et al. 2024 correction release, Zenodo 10.5281/zenodo.17968370 (Key sim results correction 20250812.xlsx Tables S10/S21; CDF_data)",
           "citation_key": "sherwin2024", "basins": {}, "all_campaigns_table_s10": s10, "all_campaigns_table_s21": s21}
    for basin, (prefix, label) in CAMPAIGNS.items():
        surv = survival_for(prefix)
        floor = s21.get(label, {}).get("min_detected_kg_h", 10.0)
        dq = detected_quantiles(surv, floor) if surv else {}
        out["basins"][basin] = {"campaign": label, **s10.get(label, {}), **s21.get(label, {}), **surv, "detected_quantiles_kg_h": dq, "detection_floor_kg_h": floor}
    OUT.write_text(json.dumps(out, indent=1))
    for b, v in out["basins"].items():
        print(b, v.get("campaign"), "loss", v.get("loss_rate_pct"), v.get("loss_rate_ci_95_pct"), "min det", v.get("min_detected_kg_h"),
              "S(x):", [round(f, 4) for f in v.get("fraction_at_or_above", [])], "detq", v.get("detected_quantiles_kg_h"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
