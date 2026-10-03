#!/usr/bin/env python
"""Fit stratum weights from GHGRP Subpart W (reporting year 2023) [ghgrp].

Reads the three tables written by ``fetch_ghgrp_subpart_w.py`` and produces
``data/fitted/strata_weights.json`` with, per basin x facility-type cell in
``configs/strata.yaml``: a facility-count weight, a throughput weight, and the
raw counts they came from.

When ``data/fitted/sites_summary.json`` exists (``build_sites.py`` [ogim]), well-pad cells take their
count and gas volume from the full OGIM site population instead of GHGRP reporters, and the three
well-pad types form one group that shares their combined GHGRP CH4 share (2026-10-03). The GHGRP
well-pad notes below then apply only to that group share.

Mapping and caveats (all recorded in the output file):

- GHGRP basins are AAPG codes; ``BASIN_MAP`` maps the codes that fall inside
  MRVSim's named basins. Everything else is ``other``. Haynesville spans
  Arkla (230) and East Texas (260); Eagle Ford is inside Gulf Coast (220),
  which also contains other plays, so its count is an upper bound.
- Onshore production "facilities" are operator-by-basin reporters, not well
  pads. The count weight for well-pad types uses **producing wells at end of
  year** as the pad proxy (pads have one to several wells, so this overstates
  pad counts uniformly, which cancels in a relative weight). Gas vs oil
  dominance is assigned per operator-basin from the energy share of reported
  sales volumes (f_gas > 0.8 gas-dominant, < 0.2 oil-dominant, else mixed),
  using EIA heat contents [eia-heat-content].
- Gathering & boosting facilities are also operator-by-basin reporters; their
  count is a lower bound on station count. Processing, transmission compression,
  and storage facilities are physical sites.
- Throughput weight: production uses gas sold (Mscf); gathering uses gas
  transported; processing uses gas received; transmission and storage have no
  consistent volume field in this table, so their throughput weight falls back
  to the count weight (flagged).
- Cells with no GHGRP rows keep a small floor weight and are flagged.

Units per Subpart W Table AA are Mscf and bbl; TODO(verify) [ghgrp].
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"
FITTED = REPO / "data" / "fitted"
YEAR = 2023

BASIN_MAP = {
    "430 - Permian Basin": "permian",
    "160A - Appalachian Basin (Eastern Overthrust Area)": "appalachian",
    "160 - Appalachian Basin": "appalachian",
    "230 - Arkla Basin": "haynesville",
    "260 - East Texas Basin": "haynesville",
    "220 - Gulf Coast Basin (LA, TX)": "eagle_ford",
    "395 - Williston Basin": "bakken",
    "540 - Denver Basin": "dj",
    "360 - Anadarko Basin": "anadarko",
    "580 - San Juan Basin": "san_juan",
    "575 - Uinta Basin": "uinta",
}
SEGMENT_MAP = {
    "Onshore petroleum and natural gas production [98.230(a)(2)]": "production",
    "Onshore petroleum and natural gas gathering and boosting [98.230(a)(9)]": "gathering",
    "Onshore natural gas processing [98.230(a)(3)]": "processing",
    "Onshore natural gas transmission compression [98.230(a)(4)]": "transmission",
    "Underground natural gas storage [98.230(a)(5)]": "storage",
}
GAS_MJ_PER_MSCF = 1000 * 1.037 * 1.05506  # 1,037 Btu/scf * 1000 scf * MJ/kBtu  [eia-heat-content]
OIL_MJ_PER_BBL = 6119.0                   # [eia-heat-content]


def main() -> int:
    emis = pd.read_csv(RAW / f"ghgrp_ef_w_emissions_source_ghg_{YEAR}.csv", low_memory=False)
    over = pd.read_csv(RAW / f"ghgrp_ef_w_facility_overview_{YEAR}.csv", low_memory=False)
    strata = yaml.safe_load((REPO / "configs" / "strata.yaml").read_text())
    sites_path = FITTED / "sites_summary.json"          # written by build_sites.py [ogim]
    sites = json.loads(sites_path.read_text()) if sites_path.exists() else None

    # One row per facility x segment: CH4 summed over reporting categories; basin from the emissions
    # table where present (production), else from the facility overview (gathering, midstream).
    emis["segment"] = emis.industry_segment.map(SEGMENT_MAP)
    emis = emis.dropna(subset=["segment"])
    fac = emis.groupby(["facility_id", "segment"], as_index=False).agg(
        ch4_t=("total_reported_ch4_emissions", "sum"), basin_raw=("basin_associated_with_facility", "first")
    )
    over["segment"] = over.industry_segment.map(SEGMENT_MAP)
    ob = over.dropna(subset=["segment"]).groupby(["facility_id", "segment"], as_index=False).agg(
        basin_over=("basin_associated_with_facility", "first")
    )
    fac = fac.merge(ob, on=["facility_id", "segment"], how="left")
    fac["basin_raw"] = fac.basin_raw.fillna(fac.basin_over)
    fac["basin"] = fac.basin_raw.map(BASIN_MAP).fillna("other")

    # Production detail: wells and sales volumes per operator-basin (Table AA(1) rows).
    prod = over[over.industry_segment.map(SEGMENT_MAP) == "production"].copy()
    prod = prod.groupby("facility_id", as_index=False).agg(
        wells=("well_producing_end_of_year", "max"),
        gas_mscf=("gas_prod_cal_year_for_sales", "max"),
        oil_bbl=("oil_prod_cal_year_for_sales", "max"),
    )
    e_gas = prod.gas_mscf.fillna(0) * GAS_MJ_PER_MSCF
    e_oil = prod.oil_bbl.fillna(0) * OIL_MJ_PER_BBL
    with np.errstate(invalid="ignore", divide="ignore"):
        prod["f_gas"] = np.where(e_gas + e_oil > 0, e_gas / (e_gas + e_oil), np.nan)
    prod["ftype"] = pd.cut(prod.f_gas, [-0.01, 0.2, 0.8, 1.01], labels=["wp_oil", "wp_mixed", "wp_gas"]).astype(object)
    prod.loc[prod.f_gas.isna(), "ftype"] = "wp_mixed"

    mid = over.groupby("facility_id", as_index=False).agg(
        gas_transported=("quant_gas_transported_gb", "max"), gas_received=("quantity_gas_received", "max")
    )
    fac = fac.merge(prod[["facility_id", "wells", "gas_mscf", "oil_bbl", "f_gas", "ftype"]], on="facility_id", how="left")
    fac = fac.merge(mid, on="facility_id", how="left")
    fac["facility_type"] = np.where(fac.segment == "production", fac.ftype, fac.segment)

    rows = []
    for cell in strata["cells"]:
        b, f = cell["basin"], cell["facility_type"]
        sel = fac[(fac.basin == b) & (fac.facility_type == f)]
        n = int(len(sel))
        if f.startswith("wp_") and sites is not None:
            # Full site population [ogim]: GHGRP covers reporters above its threshold only, which leaves out
            # most small operators (DECISION_LOG 2026-10-03).
            cell_sites = sites["cells"][f"{b}/{f}"]
            count, thr = float(cell_sites["n_sites"]), float(cell_sites["gas_mcf_yr"])
            count_basis = f"production sites with reported production in {sites['source']['production_year']} [ogim]"
            thr_basis = "gas produced at those sites (Mcf) [ogim]"
        elif f.startswith("wp_"):
            count = float(sel.wells.fillna(0).sum())
            thr = float(sel.gas_mscf.fillna(0).sum())
            count_basis, thr_basis = "producing wells at end of year", "gas produced for sales (Mscf)"
        elif f == "gathering":
            count, thr = float(n), float(sel.gas_transported.fillna(0).sum())
            count_basis, thr_basis = "GHGRP operator-basin reporters (lower bound on stations)", "gas transported through gathering (Mscf)"
        elif f == "processing":
            count, thr = float(n), float(sel.gas_received.fillna(0).sum())
            count_basis, thr_basis = "GHGRP facilities (physical plants)", "gas received (Mscf)"
        else:
            count, thr = float(n), float(n)
            count_basis, thr_basis = "GHGRP facilities (physical sites)", "no volume field; equals count weight (flagged)"
        rows.append({"basin": b, "facility_type": f, "n_ghgrp_rows": n, "count_raw": count, "throughput_raw": thr,
                     "ch4_reported_t": float(sel.ch4_t.sum()),
                     "count_basis": count_basis, "throughput_basis": thr_basis, "flag": None if n > 0 or (f.startswith("wp_") and sites is not None) else "no GHGRP rows; floor weight"})

    df = pd.DataFrame(rows)
    # Normalise within facility-type group so that type shares come from GHGRP too (raw counts across types are
    # not comparable: wells vs sites). Type shares of national CH4 are used to weight groups, a documented proxy.
    type_ch4 = fac.groupby("facility_type").ch4_t.sum()
    type_ch4 = type_ch4 / type_ch4.sum()
    # With the site table, site counts and volumes are comparable across the three well-pad types, so they
    # form one group that shares the well-pad types' combined CH4 share.
    group = df.facility_type.where(~df.facility_type.str.startswith("wp_"), "well_pad") if sites is not None else df.facility_type
    group_ch4 = type_ch4.groupby(lambda f: "well_pad" if sites is not None and f.startswith("wp_") else f).sum()
    for col in ("count_raw", "throughput_raw"):
        wcol = "weight_count" if col == "count_raw" else "weight_throughput"
        df[wcol] = 0.0
        for g, grp in df.groupby(group):
            raw = grp[col].to_numpy(dtype=float)
            floor = 0.02 * raw[raw > 0].mean() if (raw > 0).any() else 1.0
            raw = np.where(raw > 0, raw, floor)
            df.loc[grp.index, wcol] = raw / raw.sum() * float(group_ch4.get(g, 0.0))
        df[wcol] = df[wcol] / df[wcol].sum()

    out = {
        "provenance": "FITTED",
        "citation_keys": ["ghgrp", "eia-heat-content"] + (["ogim"] if sites is not None else []),
        "source_tables": [f"ghgrp_ef_w_emissions_source_ghg_{YEAR}.csv", f"ghgrp_ef_w_facility_overview_{YEAR}.csv"],
        "reporting_year": YEAR,
        "fitted_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "method": __doc__.strip(),
        "type_group_shares_from_reported_ch4": {k: float(v) for k, v in type_ch4.items()},
        "group_shares_used": {k: float(v) for k, v in group_ch4.items()},
        "basin_map": BASIN_MAP,
        "cells": df.to_dict(orient="records"),
    }
    FITTED.mkdir(parents=True, exist_ok=True)
    (FITTED / "strata_weights.json").write_text(json.dumps(out, indent=2, default=lambda o: None if pd.isna(o) else o))
    print(df[["basin", "facility_type", "n_ghgrp_rows", "count_raw", "weight_count", "weight_throughput", "flag"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
