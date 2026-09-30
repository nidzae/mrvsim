#!/usr/bin/env python
"""Fetch EPA GHGRP Subpart W facility-level data via the Envirofacts REST API [ghgrp].

Pulls three public tables for one reporting year and writes each to
``data/raw/ghgrp_<table>_<year>.csv`` with a provenance JSON:

- ``EF_W_EMISSIONS_SOURCE_GHG`` — facility x industry segment x basin, with
  reported CH4/CO2/N2O totals (basis for stratum facility-count weights).
- ``EF_W_FACILITY_OVERVIEW`` — throughput quantities (gas/oil handled,
  wells, pipeline miles) per facility (basis for throughput priors and weights).
- ``PUB_DIM_FACILITY`` — coordinates, state, county, NAICS per facility.

Table names were confirmed live on 2026-09-30 (others documented in the EPA
data dictionary returned 404). Public, no credentials required. Run:

    .venv/bin/python data/scripts/fetch_ghgrp_subpart_w.py --year 2023
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import requests

RAW = Path(__file__).resolve().parents[1] / "raw"
BASE = "https://data.epa.gov/efservice"
# (table, year filter column). Confirmed live 2026-09-30. [ghgrp]
TABLES = [
    ("EF_W_EMISSIONS_SOURCE_GHG", "REPORTING_YEAR"),
    ("EF_W_FACILITY_OVERVIEW", "REPORTING_YEAR"),
    ("PUB_DIM_FACILITY", "YEAR"),
]


def fetch(table: str, year_col: str, year: int, limit: int = 10000) -> list[dict]:
    rows: list[dict] = []
    start = 0
    while True:
        url = f"{BASE}/{table}/{year_col}/{year}/ROWS/{start}:{start + limit - 1}/JSON"
        r = requests.get(url, timeout=300)
        if r.status_code != 200:
            raise RuntimeError(f"{url} -> HTTP {r.status_code}: {r.text[:200]}")
        chunk = r.json()
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        start += limit
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2023)
    args = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    import pandas as pd

    failures = 0
    for table, year_col in TABLES:
        out = RAW / f"ghgrp_{table.lower()}_{args.year}.csv"
        try:
            rows = fetch(table, year_col, args.year)
        except Exception as e:  # noqa: BLE001
            print(f"FAILED {table}: {e}", file=sys.stderr)
            failures += 1
            continue
        pd.DataFrame(rows).to_csv(out, index=False)
        prov = {
            "source": "EPA GHGRP Envirofacts", "citation_key": "ghgrp", "table": table, "year": args.year,
            "rows": len(rows), "fetched_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "url": f"{BASE}/{table}/{year_col}/{args.year}/JSON",
        }
        out.with_suffix(".provenance.json").write_text(json.dumps(prov, indent=2))
        print(f"wrote {out.name} ({len(rows)} rows)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
