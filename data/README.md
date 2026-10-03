# Data

- `raw/` (git-ignored): downloaded public data. Scripts in `scripts/` write here with a `*.provenance.json` next to each file.
- `fitted/`: fitted priors, POD parameters, condition climatologies, validation targets, each with provenance and citation keys.
- `scripts/`: download and fitting scripts. Each documents its credentials and what it produces.

## Fetch status (2026-09-30, Phase 1)

| Script | Source | Credentials | Status |
|---|---|---|---|
| `fetch_ghgrp_subpart_w.py` | EPA GHGRP Envirofacts [ghgrp] | none | **fetched 2026-09-30**, RY2023: `EF_W_EMISSIONS_SOURCE_GHG` (49,948 rows), `EF_W_FACILITY_OVERVIEW` (6,542), `PUB_DIM_FACILITY` (11,281) |
| `fit_strata_weights.py` | derived from the above | none | **written `fitted/strata_weights.json`** (provenance FITTED, with caveats in the file: wells as pad proxy; Gulf Coast ⊇ Eagle Ford; transmission/storage throughput = count) |
| `fetch_ogim.py` | OGIM v3.0 GeoPackage, Zenodo 10.5281/zenodo.22835235 [ogim] | none | **fetched 2026-10-03** to `raw/ogim/OGIM_v3.0.gpkg` (3.4 GB, md5 checked; needed only to rebuild the site table) |
| `build_sites.py` | derived from the above (needs `shapely`) | none | **written `fitted/sites.npz`** (577,007 production sites with 2022 production, midstream location pools), `fitted/sites_summary.json`, `fitted/site_density.json`; re-run `fit_strata_weights.py` afterwards |
| `fetch_modis_cloud.py` | LAADS DAAC MOD08_M3 [modis-cloud] | `EARTHDATA_TOKEN` | not run: no token in environment |
| `fetch_era5_wind.py` | Copernicus CDS ERA5 [era5] | `~/.cdsapirc` | not run: no CDS key in environment |

Stratum weights are fitted (see above). Emission-rate, duration, throughput, and condition priors remain `configs/priors/placeholder_v0.yaml` (provenance PLACEHOLDER), so validation tests still refuse to run.
