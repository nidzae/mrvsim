#!/usr/bin/env python
"""Fetch ERA5 10 m wind (hourly, single levels) for the basin boxes and fit monthly Weibull parameters [era5].

Requires the Copernicus CDS API key in ``~/.cdsapirc`` (https://cds.climate.copernicus.eu/how-to-api)
and ``pip install cdsapi xarray netCDF4``. Downloads one year of hourly u10/v10
per basin box, computes speed, fits a Weibull(k, lambda) per basin and month by
maximum likelihood (scipy.stats.weibull_min with floc=0), and writes
``data/fitted/conditions_wind.json``.

LDAR-Sim v4 ships an equivalent downloader (vendor/ldar_sim/LDAR_Sim/src/weather/ERA5_downloader.py);
this script follows the same request shape but keeps hourly resolution.

Usage:
    .venv/bin/python data/scripts/fetch_era5_wind.py --year 2023

Without credentials the script exits with status 2 and the placeholder Weibull
parameters in configs/priors stay in force.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw" / "era5"
FITTED = REPO / "data" / "fitted"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2023)
    args = ap.parse_args()
    if not (Path.home() / ".cdsapirc").exists():
        print("~/.cdsapirc not found; cannot download ERA5. See docstring.", file=sys.stderr)
        return 2
    try:
        import cdsapi
        import numpy as np
        import xarray as xr
        from scipy import stats
    except ImportError as e:
        print(f"missing dependency: {e}. pip install cdsapi xarray netCDF4", file=sys.stderr)
        return 2
    strata = yaml.safe_load((REPO / "configs" / "strata.yaml").read_text())
    RAW.mkdir(parents=True, exist_ok=True)
    c = cdsapi.Client()
    out: dict[str, dict] = {}
    for key, b in strata["basins"].items():
        dest = RAW / f"era5_u10v10_{key}_{args.year}.nc"
        if not dest.exists():
            c.retrieve(
                "reanalysis-era5-single-levels",
                {
                    "product_type": "reanalysis", "format": "netcdf",
                    "variable": ["10m_u_component_of_wind", "10m_v_component_of_wind"],
                    "year": str(args.year), "month": [f"{m:02d}" for m in range(1, 13)],
                    "day": [f"{d:02d}" for d in range(1, 32)], "time": [f"{h:02d}:00" for h in range(24)],
                    "area": [b["lat"][1], b["lon"][0], b["lat"][0], b["lon"][1]],  # N, W, S, E
                },
                str(dest),
            )
        ds = xr.open_dataset(dest)
        speed = np.sqrt(ds["u10"] ** 2 + ds["v10"] ** 2)
        k_list, lam_list = [], []
        for m in range(1, 13):
            s = speed.sel(time=speed["time"].dt.month == m).values.ravel()
            s = s[np.isfinite(s) & (s > 0)]
            k, _, lam = stats.weibull_min.fit(s, floc=0)
            k_list.append(float(k)); lam_list.append(float(lam))
        out[key] = {"wind_weibull_k": float(np.mean(k_list)), "wind_weibull_k_monthly": k_list,
                    "wind_weibull_lambda_monthly": lam_list, "n_hours": int(speed.size)}
        print(key, "k~", round(float(np.mean(k_list)), 2))
    FITTED.mkdir(parents=True, exist_ok=True)
    (FITTED / "conditions_wind.json").write_text(json.dumps(
        {"citation_key": "era5", "year": args.year, "method": "weibull_min MLE floc=0 per basin-month", "basins": out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
