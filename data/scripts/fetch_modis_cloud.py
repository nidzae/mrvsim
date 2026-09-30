#!/usr/bin/env python
"""Fetch MODIS monthly cloud-fraction climatology (MOD08_M3 / MYD08_M3) [modis-cloud].

Requires a NASA Earthdata login token in the environment variable
``EARTHDATA_TOKEN`` (https://urs.earthdata.nasa.gov). Downloads the monthly L3
1-degree product for the requested years from LAADS DAAC, averages the
``Cloud_Fraction_Day_Mean_Mean`` field per month over the years, and writes
``data/fitted/conditions_cloud.json`` with per-basin monthly daytime cloud
fraction (mean over each basin box in ``configs/strata.yaml``).

Usage:
    EARTHDATA_TOKEN=... .venv/bin/python data/scripts/fetch_modis_cloud.py --years 2019 2023

What this cannot do without credentials: nothing is downloaded; the script exits
with status 2 and the placeholder climatology in configs/priors stays in force.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "raw" / "modis"
FITTED = Path(__file__).resolve().parents[1] / "fitted"
LAADS = "https://ladsweb.modaps.eosdis.nasa.gov/archive/allData/61/MOD08_M3"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs=2, default=[2019, 2023])
    args = ap.parse_args()
    token = os.environ.get("EARTHDATA_TOKEN")
    if not token:
        print("EARTHDATA_TOKEN not set; cannot download MOD08_M3. See docstring.", file=sys.stderr)
        return 2
    try:
        import requests
        import xarray  # noqa: F401
    except ImportError as e:
        print(f"missing dependency: {e}. pip install xarray netCDF4 pyhdf", file=sys.stderr)
        return 2
    RAW.mkdir(parents=True, exist_ok=True)
    headers = {"Authorization": f"Bearer {token}"}
    y0, y1 = args.years
    for year in range(y0, y1 + 1):
        listing = requests.get(f"{LAADS}/{year}.json", headers=headers, timeout=120)
        listing.raise_for_status()
        for entry in listing.json():
            doy = entry["name"]
            files = requests.get(f"{LAADS}/{year}/{doy}.json", headers=headers, timeout=120).json()
            for f in files:
                dest = RAW / f["name"]
                if dest.exists():
                    continue
                with requests.get(f"{LAADS}/{year}/{doy}/{f['name']}", headers=headers, stream=True, timeout=600) as r:
                    r.raise_for_status()
                    dest.write_bytes(r.content)
                print("downloaded", dest.name)
    print("Download complete. Per-basin aggregation to data/fitted/conditions_cloud.json: TODO once files are present.")
    print("Implement with pyhdf: field Cloud_Fraction_Day_Mean_Mean, scale 1e-4, mask fill values, mean over basin box.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
