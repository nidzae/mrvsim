#!/usr/bin/env python
"""Build the real-site table ``data/fitted/sites.npz`` from the OGIM database [ogim].

Reads ``data/raw/ogim/OGIM_v3.0.gpkg`` (``fetch_ogim.py``) and writes

- ``data/fitted/sites.npz``: every onshore US production site with reported 2022 production
  (lat, lon, gas Mcf/yr, oil bbl/yr, wells per site, cell index), plus a location pool per
  midstream cell; and
- ``data/fitted/site_density.json``: site counts per 0.1-degree cell, for the map's background layer; and
- ``data/fitted/sites_summary.json``: counts and volumes per cell, the method, and its caveats.
  ``fit_strata_weights.py`` reads the summary so that well-pad stratum weights come from the full
  site population rather than from GHGRP reporters only.

Method and caveats (all recorded in the summary):

- **Production records.** ``Oil_and_Natural_Gas_Production`` holds one record per reporting entity for
  production year 2022 in 20 states (wells in 14, leases or reporting units in TX, OK, KS, LA, KY, MI).
  Records with no oil, condensate, or gas in the year are dropped; -999 means "not reported" and is
  read as zero. Condensate is added to oil. Gas is the reported produced volume and is used as
  marketed gas (no separate sales volume in the table). States without production records in OGIM
  (for example AL, IL, IN, NE, TN, VA) are absent.
- **Sites.** Well-level records closer than ``LINK_M`` to each other are merged into one site
  (single-linkage); 50 m is an assumption standing in for a pad footprint, TODO(verify) [ogim].
  In old dense fields this chains many wells into one site. Lease-level records are one site each at
  the location OGIM gives for the lease.
- **Wells per lease.** Texas gas records (6-digit ids, condensate but no crude) are single gas wells.
  For Texas oil leases (5-digit ids) and for the lease or unit records of OK, KS, LA, KY and MI the
  well count is not in the production table. It is estimated by assigning every producing well of
  the state in ``Oil_and_Natural_Gas_Wells`` (Texas: oil-type wells that are not plugged, shut in,
  permitted or injecting) to a production record: the nearest record of the same operator among the
  ``LEASE_K`` nearest within ``LEASE_RADIUS_KM``, else the nearest record within that radius. A record
  with no assigned well counts one. This is an MRVSim estimate, TODO(verify) [ogim].
- **Basin.** Point-in-polygon against the basin and play outlines in ``Oil_and_Natural_Gas_Basins``
  (EIA sedimentary basins and tight-oil/shale-gas plays, as carried by OGIM); everything else is ``other``.
- **Facility type.** Energy share of gas at the site: > 0.8 gas-dominant, < 0.2 oil-dominant, else
  mixed, with the heat contents in ``configs/constants.yaml`` [eia-heat-content] (same rule as
  ``fit_strata_weights.py``).
- **Cells.** ``configs/strata.yaml`` lists the basin x type cells. Sites whose (basin, type) is not a
  listed cell are folded into the listed well-pad cell of the same basin with the most sites; they
  keep their own location and production.
- **Midstream pools.** Locations only: gathering compressor stations, transmission (booster and
  transmission) compressor stations, gas processing plants, and underground storage fields.

Run:

    .venv/bin/python data/scripts/build_sites.py
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import shapely
import yaml
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from shapely import wkb

REPO = Path(__file__).resolve().parents[2]
GPKG = REPO / "data" / "raw" / "ogim" / "OGIM_v3.0.gpkg"
FITTED = REPO / "data" / "fitted"
US = "UNITED STATES OF AMERICA"
PROD_YEAR = 2022
LINK_M = 50.0                 # wells closer than this are one site; assumption, TODO(verify) [ogim]
EARTH_RADIUS_KM = 6371.0
LEASE_RADIUS_KM = 2.0         # a well further than this from every lease is not assigned; assumption [ogim]
LEASE_K = 4                   # nearest leases searched for one of the same operator
DENSITY_DEG = 0.1             # grid of the site-density layer shown behind the sampled facilities on the map
# OGIM basin / play name -> MRVSim basin key. Outlines are EIA's, carried by OGIM. [ogim]
BASIN_NAMES = {
    "PERMIAN": "permian", "APPALACHIAN": "appalachian", "HAYNESVILLE-BOSSIER": "haynesville", "EAGLE FORD": "eagle_ford",
    "BAKKEN": "bakken", "DENVER": "dj", "ANADARKO": "anadarko", "SAN JUAN": "san_juan", "UINTA-PICEANCE": "uinta",
}
WP_TYPES = ("wp_oil", "wp_mixed", "wp_gas")
# Midstream location pools: facility type -> (table, SQL filter on FAC_TYPE or None). [ogim]
MIDSTREAM = {
    "gathering": ("Natural_Gas_Compressor_Stations", "FAC_TYPE = 'GATHERING COMPRESSOR STATION'"),
    "transmission": ("Natural_Gas_Compressor_Stations",
                     "FAC_TYPE in ('TRANSMISSION COMPRESSOR STATION', 'BOOSTER PUMPING STATION, NATURAL GAS TRANSPORTATION')"),
    "processing": ("Gathering_and_Processing", None),
    "storage": ("Injection_and_Disposal", "FAC_TYPE in ('DEPLETED FIELD', 'AQUIFER', 'SALT DOME')"),
}


def _geometry(blob: bytes):
    """Decode a GeoPackage geometry blob (header + WKB)."""
    envelope_bytes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(blob[3] >> 1) & 7]
    return wkb.loads(bytes(blob[8 + envelope_bytes:]))


def _basin_of(con: sqlite3.Connection, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    names = ",".join(f"'{n}'" for n in BASIN_NAMES)
    out = np.full(lon.shape[0], "other", dtype=object)
    for name, blob in con.execute(f"select NAME, geom from Oil_and_Natural_Gas_Basins where NAME in ({names})"):
        inside = shapely.contains_xy(_geometry(blob), lon, lat)
        out[inside & (out == "other")] = BASIN_NAMES[name]
    return out


def _xyz_km(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    la, lo = np.deg2rad(lat), np.deg2rad(lon)
    return EARTH_RADIUS_KM * np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def _wells_per_lease(con: sqlite3.Connection, rec: pd.DataFrame) -> np.ndarray:
    """Estimated wells behind each production record (1 for well-level records and Texas gas wells)."""
    n = np.ones(len(rec), dtype=np.int64)
    is_lease = (rec.entity != "WELL").to_numpy()
    tx_gas = ((rec.state == "TEXAS") & (rec.fac_id.astype(str).str.len() == 6)).to_numpy()
    for state in sorted(rec.state[is_lease].unique()):
        rows = np.nonzero(is_lease & (rec.state == state).to_numpy() & ~tx_gas)[0]
        if rows.size == 0:
            continue
        where = ("OGIM_STATUS = 'N/A' and FAC_TYPE in ('OIL WELL', 'OIL/GAS WELL')" if state == "TEXAS" else "OGIM_STATUS = 'PRODUCING'")
        wells = pd.read_sql(f"select OPERATOR op, LATITUDE lat, LONGITUDE lon from Oil_and_Natural_Gas_Wells "
                            f"where COUNTRY = '{US}' and STATE_PROV = '{state}' and ON_OFFSHORE = 'ONSHORE' and {where}", con).dropna(subset=["lat", "lon"])
        if wells.empty:
            continue
        k = min(LEASE_K, rows.size)
        dist, idx = cKDTree(_xyz_km(rec.lat.to_numpy()[rows], rec.lon.to_numpy()[rows])).query(_xyz_km(wells.lat.to_numpy(), wells.lon.to_numpy()), k=k)
        dist, idx = dist.reshape(len(wells), k), idx.reshape(len(wells), k)
        same = (rec.op.to_numpy()[rows][idx] == wells.op.to_numpy()[:, None]) & (dist <= LEASE_RADIUS_KM)
        choice = np.where(same.any(axis=1), same.argmax(axis=1), 0)
        target = idx[np.arange(len(wells)), choice]
        ok = dist[np.arange(len(wells)), choice] <= LEASE_RADIUS_KM
        n[rows] = np.maximum(np.bincount(target[ok], minlength=rows.size), 1)
    return n


def _merge_wells(lat: np.ndarray, lon: np.ndarray, link_m: float) -> np.ndarray:
    """Single-linkage site labels for points closer than ``link_m`` (chord distance on the sphere)."""
    la, lo = np.deg2rad(lat), np.deg2rad(lon)
    xyz = EARTH_RADIUS_KM * np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])
    pairs = cKDTree(xyz).query_pairs(link_m / 1000.0, output_type="ndarray")
    n = lat.shape[0]
    graph = coo_matrix((np.ones(len(pairs), dtype=np.int8), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
    return connected_components(graph, directed=False)[1]


def main() -> int:
    if not GPKG.exists():
        print(f"missing {GPKG}; run data/scripts/fetch_ogim.py first", file=sys.stderr)
        return 1
    constants = yaml.safe_load((REPO / "configs" / "constants.yaml").read_text(encoding="utf-8"))
    strata = yaml.safe_load((REPO / "configs" / "strata.yaml").read_text(encoding="utf-8"))
    mj_per_mcf = float(constants["gas_hhv_mj_per_m3"]) * 28.316847      # 1 Mcf = 28.316847 m3 (exact)
    mj_per_bbl = float(constants["oil_mj_per_bbl"])
    con = sqlite3.connect(f"file:{GPKG}?mode=ro", uri=True)

    rec = pd.read_sql(
        "select STATE_PROV state, ENTITY_TYPE entity, FAC_ID fac_id, OPERATOR op, OIL_BBL oil, GAS_MCF gas, CONDENSATE_BBL cond, LATITUDE lat, LONGITUDE lon "
        f"from Oil_and_Natural_Gas_Production where COUNTRY = '{US}' and ON_OFFSHORE = 'ONSHORE' and PROD_YEAR = {PROD_YEAR}", con)
    n_records = len(rec)
    rec["oil"] = rec.oil.clip(lower=0) + rec.cond.clip(lower=0)
    rec["gas"] = rec.gas.clip(lower=0)
    rec = rec[(rec.oil > 0) | (rec.gas > 0)].reset_index(drop=True)

    is_well = (rec.entity == "WELL").to_numpy()
    site = np.empty(len(rec), dtype=np.int64)
    labels = _merge_wells(rec.lat.to_numpy()[is_well], rec.lon.to_numpy()[is_well], LINK_M)
    site[is_well] = labels
    site[~is_well] = labels.max() + 1 + np.arange((~is_well).sum())
    rec["site"] = site
    rec["wells"] = _wells_per_lease(con, rec)
    sites = rec.groupby("site", sort=True).agg(lat=("lat", "mean"), lon=("lon", "mean"), oil=("oil", "sum"), gas=("gas", "sum"),
                                               wells=("wells", "sum"), state=("state", "first"), entity=("entity", "first")).reset_index(drop=True)
    sites["basin"] = _basin_of(con, sites.lon.to_numpy(), sites.lat.to_numpy())
    e_gas, e_oil = sites.gas * mj_per_mcf, sites.oil * mj_per_bbl
    f_gas = e_gas / (e_gas + e_oil)
    sites["ftype"] = np.where(f_gas > 0.8, "wp_gas", np.where(f_gas < 0.2, "wp_oil", "wp_mixed"))
    sites["boe_d"] = (sites.oil + e_gas / mj_per_bbl) / 365.0

    # Fold (basin, type) combinations that are not listed cells into the basin's largest listed well-pad cell.
    listed = {(c["basin"], c["facility_type"]) for c in strata["cells"] if c["facility_type"] in WP_TYPES}
    raw_counts = sites.groupby(["basin", "ftype"]).size()
    sites["cell_type"] = sites.ftype
    folded: dict[str, str] = {}
    for (basin, ftype), n in raw_counts.items():
        if (basin, ftype) in listed:
            continue
        options = [(raw_counts.get((basin, t), 0), t) for t in WP_TYPES if (basin, t) in listed]
        if not options:
            raise ValueError(f"configs/strata.yaml lists no well-pad cell for basin {basin!r}")
        target = max(options)[1]
        sites.loc[(sites.basin == basin) & (sites.ftype == ftype), "cell_type"] = target
        folded[f"{basin}/{ftype}"] = f"{basin}/{target} ({int(n)} sites)"
    sites["cell"] = sites.basin + "/" + sites.cell_type
    cell_keys = sorted(sites.cell.unique())
    cell_idx = sites.cell.map({k: i for i, k in enumerate(cell_keys)}).to_numpy(dtype=np.int16)

    arrays: dict[str, np.ndarray] = {
        "lat": sites.lat.to_numpy(np.float32), "lon": sites.lon.to_numpy(np.float32),
        "gas_mcf_yr": sites.gas.to_numpy(np.float32), "oil_bbl_yr": sites.oil.to_numpy(np.float32),
        "n_wells": np.minimum(sites.wells.to_numpy(), np.iinfo(np.uint16).max).astype(np.uint16),
        "cell_idx": cell_idx, "cell_keys": np.array(cell_keys),
    }

    pools: dict[str, int] = {}
    midstream_cells = [(c["basin"], c["facility_type"]) for c in strata["cells"] if c["facility_type"] in MIDSTREAM]
    for ftype, (table, where) in MIDSTREAM.items():
        q = f"select LATITUDE lat, LONGITUDE lon from {table} where COUNTRY = '{US}' and ON_OFFSHORE = 'ONSHORE' and OGIM_STATUS != 'ABANDONED'"
        fac = pd.read_sql(q + (f" and {where}" if where else ""), con).dropna()
        fac["basin"] = _basin_of(con, fac.lon.to_numpy(), fac.lat.to_numpy())
        named = {b for b, f in midstream_cells if f == ftype and b != "other"}
        for basin, f in midstream_cells:
            if f != ftype:
                continue
            sel = fac[fac.basin == basin] if basin != "other" else fac[~fac.basin.isin(named)]
            if len(sel):
                arrays[f"pool:{basin}/{ftype}"] = sel[["lat", "lon"]].to_numpy(np.float32)
                pools[f"{basin}/{ftype}"] = int(len(sel))

    FITTED.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(FITTED / "sites.npz", **arrays)

    # Site counts per DENSITY_DEG cell (cell centre lon, lat, count) for the map's background layer.
    gx = np.floor(sites.lon.to_numpy() / DENSITY_DEG).astype(np.int64); gy = np.floor(sites.lat.to_numpy() / DENSITY_DEG).astype(np.int64)
    grid = pd.DataFrame({"gx": gx, "gy": gy}).groupby(["gx", "gy"]).size().reset_index(name="n")
    density = {"citation_keys": ["ogim"], "cell_deg": DENSITY_DEG, "n_sites": int(len(sites)),
               "cells": [[round((x + 0.5) * DENSITY_DEG, 2), round((y + 0.5) * DENSITY_DEG, 2), int(n)] for x, y, n in grid.itertuples(index=False)]}
    (FITTED / "site_density.json").write_text(json.dumps(density, separators=(",", ":")) + "\n", encoding="utf-8")

    marginal = sites.boe_d < 15.0
    by_cell = sites.groupby("cell").agg(n_sites=("gas", "size"), gas_mcf_yr=("gas", "sum"), oil_bbl_yr=("oil", "sum"), n_wells=("wells", "sum"),
                                        median_boe_d=("boe_d", "median"), share_under_15_boe_d=("boe_d", lambda s: float((s < 15.0).mean())))
    summary = {
        "provenance": "FITTED",
        "citation_keys": ["ogim", "eia-heat-content"],
        "source": {"file": GPKG.name, "doi": "10.5281/zenodo.22835235", "production_year": PROD_YEAR},
        "built_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "method": __doc__.strip(),
        "link_distance_m": LINK_M,
        "n_production_records": int(n_records), "n_records_with_production": int(len(rec)), "n_sites": int(len(sites)),
        "largest_site_wells": int(sites.wells.max()),
        "wells_per_site_by_state": {st: {"sites": int(len(g)), "wells": int(g.wells.sum()), "mean": float(g.wells.mean())} for st, g in sites.groupby("state")},
        "lease_radius_km": LEASE_RADIUS_KM,
        "records_by_state_and_entity": {f"{s}/{e}": int(n) for (s, e), n in rec.groupby(["state", "entity"]).size().items()},
        "sites_with_zero_gas": int((sites.gas == 0).sum()),
        "under_15_boe_d": {"share_of_sites": float(marginal.mean()), "share_of_gas": float(sites.gas[marginal].sum() / sites.gas.sum()),
                           "share_of_oil": float(sites.oil[marginal].sum() / sites.oil.sum())},
        "folded_cells": folded,
        "cells": {k: {c: (float(v) if isinstance(v, float) else int(v)) for c, v in row.items()} for k, row in by_cell.to_dict(orient="index").items()},
        "midstream_pools": pools,
    }
    (FITTED / "sites_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(by_cell.round(3).to_string())
    print(f"sites: {len(sites)}; folded: {folded}; pools: {pools}")
    print(f"under 15 boe/d: {summary['under_15_boe_d']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
