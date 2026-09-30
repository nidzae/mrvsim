# Data

- `raw/` (git-ignored): downloaded public data. Scripts in `scripts/` write here with a `*.provenance.json` next to each file.
- `fitted/`: fitted priors, POD parameters, condition climatologies, validation targets, each with provenance and citation keys.
- `scripts/`: download and fitting scripts. Each documents its credentials and what it produces.

## Fetch status (2026-09-30, Phase 1)

| Script | Source | Credentials | Status |
|---|---|---|---|
| `fetch_ghgrp_subpart_w.py` | EPA GHGRP Envirofacts [ghgrp] | none | **fetched 2026-09-30**, RY2023: `EF_W_EMISSIONS_SOURCE_GHG` (49,948 rows), `EF_W_FACILITY_OVERVIEW` (6,542), `PUB_DIM_FACILITY` (11,281) |
| `fit_strata_weights.py` | derived from the above | none | **written `fitted/strata_weights.json`** (provenance FITTED, with caveats in the file: wells as pad proxy; Gulf Coast ⊇ Eagle Ford; transmission/storage throughput = count) |
| `fetch_modis_cloud.py` | LAADS DAAC MOD08_M3 [modis-cloud] | `EARTHDATA_TOKEN` | not run: no token in environment |
| `fetch_era5_wind.py` | Copernicus CDS ERA5 [era5] | `~/.cdsapirc` | not run: no CDS key in environment |

Stratum weights are fitted (see above). Emission-rate, duration, throughput, and condition priors remain `configs/priors/placeholder_v0.yaml` (provenance PLACEHOLDER), so validation tests still refuse to run.
