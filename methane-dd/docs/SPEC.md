# Supply-Chain Methane Due Diligence Tool — Build Spec

Oct 9, 2026 · @Nidhi

## 1. Purpose and limits

The tool turns an area of interest along a gas supply chain into a defensible methane score with error bounds. It does this by pulling every public plume detection **and every observation that saw nothing**, attributing them to assets, and running the duty-factor statistics from our earlier analysis.

**What public data can support (high confidence):**

- A **measured floor**: emissions from large sources that were seen. This is a lower bound on true emissions.
- A **large-source ceiling**: how much large-source emission could have gone unseen, given how many clear looks found nothing.
- **Disqualification**: a supplier whose measured floor alone exceeds the target fails, regardless of certificates.

**What public data cannot support (high confidence):**

- Certification that an asset meets 0.2%. Sources below roughly 100 kg/h are invisible to satellites. At a typical well pad the entire 0.2% budget is about 1.4 kg/h.
- A positive claim therefore requires supplier-provided or commissioned low-threshold measurement (Section 3). The tool ingests that data when it exists and labels the score as unverified when it does not.

**Scope (v1):** US onshore, methane only, every segment between the wellhead and the customer's meter: production, gathering, processing, transmission, storage, and local distribution whenever the customer takes gas from a utility. LNG liquefaction, shipping and regasification form an optional segment, switched on only when the contract path includes them.

**Relationship to the MRV OSSE tool:** the sensor models (detection-probability curves, quantification error) are shared. This tool runs them retrospectively on real observation records; the OSSE runs them prospectively on simulated ones.

## 2. Data sources

The binding gap is not plume lists but **observation records**: the statistics need every clear-sky look at an asset, including the ones that saw nothing. Sources that publish only detections can raise the floor but cannot shrink the ceiling.

### 2a. Plume and observation data

| Source | Sensors | What it gives | Approx. detection limit | Non-detection record? | Access | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| [MAPL-EMIT](https://arxiv.org/abs/2604.10094) (Google Research and NASA JPL, released September 2026) | EMIT radiance, deep-learning retrieval | Per-pixel enhancement, plume outline and **source location**, including overlapping plumes; confidence labels from spectral-fit scores. Finds about 1.5× as many plumes as human analysts and recovers 84% of NASA L2B plumes | Lower than the matched-filter L2B product; no published kg/h figure found | Yes: the released inference library can be run on every EMIT granule over the area, giving one consistent detector for detections and non-detections | Plume database and app on Earth Engine; model on Kaggle; inference code on GitHub ([Google announcement](https://blog.google/innovation-and-ai/models-and-research/google-research/mapping-global-methane-emissions-from-space/)) | High on capability; emission rates in kg/h not confirmed in the release, so verify or compute from enhancement plus wind |
| [Carbon Mapper](https://carbonmapper.org/articles/product-guide) | Tanager-1 (since Aug 2024); AVIRIS-NG, GAO and AEMIS aircraft; EMIT | Plumes and sources with rates and uncertainties; attribution to facility and equipment type; quarterly Tanager tasking of major basins in 2025–26 | Tanager-1: 90% detection near 100 kg/h, minimum about 64–144 kg/h by imaging mode ([Duren et al. 2025](https://amt.copernicus.org/articles/18/6933/2025/amt-18-6933-2025.pdf)); AEMIS aircraft 1–5 kg/h ([IWGGMS 2026](https://atmosphere.copernicus.eu/sites/default/files/custom-uploads/IWGGMS%2022%20event/Day1/1.06_Duren.pdf)) | Likely, via scene footprints in the STAC catalogue; verify | Free account; [API docs](https://api.carbonmapper.org/api/v1/docs) | High |
| [EMIT L2B CH4PLM V002](https://www.earthdata.nasa.gov/data/catalog/lpcloud-emitl2bch4plm-002) | EMIT, 60 m | Manually reviewed plume complexes with emission rate and uncertainty | \~hundreds of kg/h | Yes, via granule footprints in NASA CMR | NASA Earthdata; open | High; use as a cross-check on MAPL-EMIT |
| [UNEP IMEO MARS](https://methanedata.unep.org/download-dataset) | Multi-satellite | Validated plumes with flux and uncertainty; persistency-weighted flux | \~1,000+ kg/h multispectral; lower hyperspectral | No | CSV, GeoJSON, API; [licence states no commercial use](https://methanedata.unep.org/table_colum_deffinition_v1.pdf) | High |
| [MethaneSAT](https://developers.google.com/earth-engine/datasets/publisher/edf-methanesat-ee) | MethaneSAT, MethaneAIR | L4 point sources; L4 **area sources**; basin intensities ([ACP 2026](https://acp.copernicus.org/articles/26/5961/2026/)) | Point sources \~hundreds of kg/h | Partial, via L3 coverage | Earth Engine on request | High; archive May 2024 to June 2025 only |
| SRON TROPOMI plume maps | Sentinel-5P, 7 km | Very large plumes, weekly | Several t/h | No | Open | Moderate; basin context only |
| GHGSat | GHGSat constellation | Facility plumes; tasking on request | \~100 kg/h | Yes, for purchased tasking | Commercial | Moderate |
| Academic aerial | Kairos (Sherwin et al. 2024); Permian campaigns; EEMDL supply-chain campaigns | Site-level rates | \~1–10 kg/h by platform | Yes within campaigns | Supplementary data; often anonymised | Moderate on public site resolution |

### 2b. Asset, throughput and reported data

| Need | Source | Note |
| --- | --- | --- |
| Infrastructure locations | EDF OGIM database; EIA energy atlas; state well databases | Asset discovery inside the area of interest and neighbour checks |
| Contract path mapping | EEMDL supply-chain mapping work (Allen and Snyder) | Method for tracing which segments carry a given volume |
| Production (upstream denominator) | Texas RRC, New Mexico OCD, other state agencies | Lease or well level, monthly |
| Pipeline throughput | Pipeline electronic bulletin boards; FERC filings; EIA | Point-level flows on interstate lines |
| Distribution system | PHMSA gas distribution annual reports; utility Subpart W | Miles of main by material, leak counts; satellites see almost none of this segment |
| Reported emissions | EPA GHGRP Subpart W | The operator's own inventory, compared against the measured floor |
| Supplier data | MiQ audit reports, OGMP 2.0 reports, continuous-monitor and aerial results | Only source of sub-threshold evidence; Section 3 |

*Detection limits above are order-of-magnitude figures from operator claims and blind tests. The tool must use per-sensor detection-probability curves, not single thresholds.*

### 2c. Research to track

The groups below publish the methods this tool should reuse rather than reinvent. Most present at the [EEMDL 2026 Annual Event](https://cvent.utexas.edu/event/17d21fcf-0071-4fd5-9e79-9cb0f71d8cb1/speakers), Austin, October 13–15, 2026, whose [workshops](https://cvent.utexas.edu/event/17d21fcf-0071-4fd5-9e79-9cb0f71d8cb1/workshops) cover measurement-informed inventories and mapping domestic gas supply chains.

| Group | People | Use for this tool |
| --- | --- | --- |
| EEMDL, UT Austin | Arvind Ravikumar, David Allen, Erin Tullos, Jen Snyder | Measurement-informed inventories; supply-chain mapping; survey-frequency effects on accuracy; a [US LNG chain study](https://www.cambridge.org/engage/chemrxiv/article-details/6882ca69fc5f0acb52e159e3) finding stages after production carry up to 73% of production-to-liquefaction intensity |
| Colorado School of Mines | Dorit Hammerling | Turning continuous-monitor data into measurement-derived inventories for OGMP 2.0 |
| Colorado State University METEC | Dan Zimmerle, Anna Hodshire, Michael Moy | Controlled-release test results: the source for detection-probability curves |
| Carbon Mapper | Riley Duren, Daniel Cusworth, Alana Ayasse | Super-emitter intensities by basin using a [hierarchical Bayesian model for intermittency and detection limits](https://meetingorganizer.copernicus.org/EGU26/EGU26-5831.html): the closest published analogue to Section 6 |
| Google Research and NASA JPL | Vishal Batchu, Andrew Thorpe, Philip Brodrick | MAPL-EMIT detection and source localisation |
| Highwood Emissions | Jeff Rutherford | Reconciling measurements with inventories |
| Bridger Photonics | Chris Donahue | Aerial LiDAR detection-probability data |
| Stanford and collaborators | Evan Sherwin, Adam Brandt | Blind satellite tests; national aerial survey |

## 3. Due diligence protocol

The cleanest gas available today is gas whose supplier will hand over measurement data and accept contract terms tied to it. Public data screens suppliers; supplier data is what verifies them.

### 3a. Requests to every operator on the contract path

- [ ] Asset list with coordinates, operator, and the share of customer volume each asset handles
- [ ] Monthly production or throughput per asset for the last 3 years
- [ ] EPA Subpart W reports for the same period
- [ ] MiQ audit reports, including the monitoring-technology tier, not only the grade
- [ ] OGMP 2.0 reporting level and the Level 5 reconciliation, if any
- [ ] All aerial survey and continuous-monitor results, including raw detections and survey dates
- [ ] Event logs: blowdowns, flaring malfunctions, tank-hatch and pressure-relief events, with start and end times
- [ ] Equipment inventory: pneumatic controllers (zero-bleed or not), tank vapour control, compressor seal type, flare monitoring
- [ ] Response record for any third-party plume notification (IMEO MARS, EPA Super Emitter Program, Carbon Mapper)

### 3b. Measurement to commission where the supplier has none

- Aerial surveys at a 90%-detection limit of 1–3 kg/h, two to four times per year, across all contracted upstream and gathering assets.
- Continuous monitoring at compressor stations and processing plants on the path.
- Monthly tasked satellite looks (for example GHGSat) at the largest midstream nodes, where budgets in kg/h are large enough for satellites to inform compliance.

### 3c. Contract terms

- Data-sharing and audit rights for all of 3a, refreshed annually.
- A remediation deadline for any detection, with documented root cause and duration.
- Price or volume adjustment tied to the tool's assured intensity (Section 7), not to the certificate grade.

A supplier that refuses 3a should be scored on public data alone, which caps its grade at "screened, unverified" (Section 7).

## 4. Inputs

The user supplies an **area of interest**, not a finished asset list. The tool finds the infrastructure inside it, and the user confirms which assets sit on the contract path.

### 4a. Area of interest

| Format | Geometry accepted | Typical use |
| --- | --- | --- |
| KMZ or KML | Points, lines, polygons, folders | Drawn in Google Earth: a production-field polygon, pipeline routes, compressor points, the customer site |
| Coordinates | Pasted list or CSV of latitude, longitude (WGS84), with optional label and radius in metres | Quick runs on a few sites |
| GeoJSON or shapefile | Same as KMZ | Users with GIS files |

- Points get a default 500 m radius; lines get a 250 m corridor on each side; polygons are used as drawn.
- One point must be flagged as the **customer site**. Its fields are the annual volume delivered, the delivery type (direct connection to transmission, or through a utility), and the utility name if any.
- KMZ folder names are read as segment hints (for example a folder named "gathering").

### 4b. Asset discovery and confirmation

The tool intersects the area with OGIM and state well data and proposes an asset list on the map. The user accepts, removes, or adds assets and fills the fields below.

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| asset\_id | string | yes | Unique key, generated on discovery |
| name | string | yes | Display name |
| segment | enum | yes | production, gathering, processing, transmission, storage, distribution, lng (optional) |
| operator | string | yes | Company operating the asset |
| geometry | Point, Polygon or LineString | yes | From the area file or from discovery |
| throughput | number | yes | Annual throughput, with units and methane fraction |
| throughput\_source | string | yes | Where the number came from and for which period |
| customer\_share | number 0–1 | yes | Fraction of this asset's throughput carrying the customer's volume |
| supplier\_measurements | path | no | CSV of supplier aerial or continuous-monitor results |
| event\_log | path | no | CSV of documented events with start and end times |

Three validation rules matter most:

- Pipelines longer than about 50 km are split so that compressor stations along them become separate assets.
- Every throughput value names its source and period, because the denominator moves the KPI as much as the numerator.
- The customer\_share values along the path must be consistent with the customer's delivered volume, or the delivered intensity is wrong.

## 5. Pipeline architecture

The pipeline runs in seven stages, each writing a GeoParquet table so any stage can be rerun alone.

1. **Load area and discover assets.** Parse the KMZ, coordinates or GeoJSON; build buffers; propose assets from OGIM and state data; record the user's confirmations.
2. **Ingest detections.** Query each source by the area's bounding box and the date window: MAPL-EMIT plume database, Carbon Mapper, EMIT L2B, IMEO, MethaneSAT. Normalise to one plume table: source, sensor, timestamp (UTC), source location, emission rate, rate uncertainty, wind source.
3. **Ingest observations.** Build one look table: sensor, timestamp, footprint, clear-sky fraction over each asset, wind speed. Sources:
   - EMIT: run the MAPL-EMIT inference library on every EMIT granule over the area, so detections and non-detections come from the same detector.
   - Carbon Mapper: scene footprints from the STAC catalogue.
   - MethaneSAT: L3 scene coverage.
   - Supplier surveys: dates and sites from the supplier file.
4. **Quantify where needed.** If a plume has enhancement but no emission rate (possible for MAPL-EMIT), compute the rate from integrated mass enhancement and reanalysis wind, carrying the wind error.
5. **Deduplicate.** Merge detections of the same event seen by two sensors within 1 hour and 200 m; keep both rate estimates.
6. **Attribute.** Use MAPL-EMIT or Carbon Mapper source locations where given, otherwise plume origin; weight down when other infrastructure sits nearer. Store an attribution probability.
7. **Estimate.** Run the statistical model (Section 6) per asset.
8. **Score and report.** Produce segment intensities and delivered intensity (Section 7).

All raw API responses are cached with their retrieval date, so a score can be reproduced exactly later.

## 6. Statistical model

Each asset gets three emission components, estimated separately because they rest on different evidence: detected large sources, undetected large sources, and sub-threshold emissions.

### 6a. Duty factor with imperfect detection

For each clear look j at an asset, the chance of a detection depends on the source being on (duty factor p) and on the sensor catching it (detection probability POD, which depends on emission rate Q and wind speed u). The likelihood over all looks is:

```latex
L(p, Q) = \prod_j \left[p \cdot \mathrm{POD}_j(Q, u_j)\right]^{d_j} \left[1 - p \cdot \mathrm{POD}_j(Q, u_j)\right]^{1-d_j}
```

Here d\_j is 1 for a detection and 0 otherwise. There is no closed form, so the posterior is computed on a grid over p (1,000 points) for each draw of Q. Ignoring POD would treat missed plumes as "off" and bias p low.

- **POD curves:** logistic in Q/u per sensor, fitted to published controlled-release results. Store parameters in a config file with their citation.
- **Prior on p:** default Jeffreys, Beta(0.5, 0.5); option for an informative prior from fleet revisit data. Every report states which prior was used and shows the result under the uniform prior too.

### 6b. Emission rate

- Two or more detections: fit a lognormal to the measured rates, inflating each by its reported uncertainty.
- One detection: use that rate with its uncertainty.
- Zero detections: draw Q from the size distribution of detected sources in the same basin and segment, truncated at the sensor's detection range.

### 6c. Annual emissions and the three components

```latex
E_{\text{large}} = 8760 \cdot p \cdot \bar{Q}
```

- **Floor:** E\_large for assets with detections, with p and Q from their posteriors.
- **Large-source ceiling:** for assets with no detections, the 95th percentile of E\_large under the zero-detection posterior. With n clear looks and good detection probability this falls roughly as 3/n (the rule of three).
- **Sub-threshold:** in order of preference: supplier low-threshold measurements; MethaneSAT L4 area flux for the grid cell, shared out by production; a segment-level literature fraction. The report labels which one was used, because the third is a prior, not a measurement.

### 6d. Monte Carlo roll-up

Run 10,000 draws. In each draw, sample p, Q and attribution for every asset; sum the three components; divide by throughput; weight by customer\_share. The output is a distribution of chain intensity, from which percentiles are read.

### 6e. Known weaknesses to handle explicitly

- **Time-of-day bias:** satellite looks cluster near midday in clear weather. Flag assets where supplier event logs show night or storm events.
- **Trend:** emissions change after repairs. Report by year and weight recent years; never average 2019 and 2026 together silently.
- **Attribution:** in dense fields a plume may belong to a neighbour. Carry the attribution probability through the Monte Carlo rather than dropping uncertain plumes.

### 6f. Segment-specific handling

- **Distribution:** emissions come from thousands of small leaks that no satellite and few aircraft detect. Estimate them from the utility's Subpart W report and PHMSA main-mile and leak data, scaled by published mobile-survey studies, and label the result as a prior unless the utility supplies survey data. Distribution can therefore cap a path at "Screened" even when upstream is verified.
- **LNG (optional):** use facility aerial and continuous-monitor results from the liquefaction operator; EEMDL and Cheniere-funded studies provide the method.
- **Benchmark:** compare per-basin results against Carbon Mapper's published hierarchical model before trusting this tool's numbers.

## 7. KPI

The headline KPI is **assured intensity**: the 95th percentile of chain methane intensity, reported next to an **evidence grade** saying how much of that number is measured rather than assumed. A single point-estimate percentage is rejected because it hides exactly the uncertainty MiQ is criticised for hiding.

### 7a. Definitions

- **Segment intensity** = methane emitted by a segment on the customer's share of its throughput ÷ methane throughput of that share. It locates the bad parts of the path, so a single operator or asset can be swapped out or required to measure.
- **Delivered intensity** = all methane emitted along the path on the customer's volume ÷ methane delivered at the customer's meter. This is the number that enters the customer's greenhouse-gas inventory as upstream fuel emissions (GHG Protocol Scope 3, Category 3), and it drives the go or no-go decision.
- **Units reported for both:** percent of methane; kg CH4 per MMBtu delivered; g CO2e per MJ, using the global-warming potential the customer reports with (for example IPCC AR6 GWP100 for fossil methane, 29.8).
- **Assured intensity** = 95th percentile of the Monte Carlo result, computed for each segment and for delivered intensity.
- **Measured floor** = 5th percentile of the floor component alone. If it exceeds the target, the segment or path fails on measured evidence.
- **Evidence share** = fraction of the assured intensity coming from measured components rather than priors.

```latex
I_{\text{delivered}} = \frac{\sum_{s} \sum_{a \in s} \text{share}_a \cdot E_a}{M_{\text{CH}_4,\ \text{delivered}}}
```

Here E\_a is the annual emission of asset a, share\_a its customer\_share, and the denominator the methane mass delivered to the customer per year. Because gas is lost along the way, the delivered denominator is slightly smaller than upstream throughput, so delivered intensity is never lower than the sum of segment intensities.

### 7b. Grades

| Grade | Rule | Meaning for the buyer |
| --- | --- | --- |
| Verified | Assured intensity ≤ target and evidence share ≥ 80% with supplier low-threshold data | Defensible claim of meeting the target |
| Screened, unverified | No disqualifying detections, but sub-threshold emissions rest on priors | Better than average evidence of no super-emitters; no claim on total intensity |
| Flagged | Detections present; measured floor below target but assured intensity above it | Request event logs and remediation before buying |
| Failed | Measured floor above target | Exclude, or price as high-intensity gas |

The report also gives each asset's contribution to assured intensity, so the buyer can see which single asset to swap out or demand measurement from.

**Two outputs, two decisions.** Segment grades guide reconfiguration: replace or demand measurement from the segments that fail. The delivered-intensity grade drives the recommendation: go if Verified; conditional go if Screened with a contracted measurement plan; no-go if Failed. The per-segment view is also directly comparable with MiQ's per-segment grades.

## 8. Build plan for Claude Code

Build in Python as a command-line pipeline first, with a thin map interface added once the numbers are trusted.

### 8a. Stack

- Data: geopandas, shapely, pyproj, fastkml for KMZ; GeoParquet files plus DuckDB for queries.
- Ingest: earthengine-api (MAPL-EMIT database, MethaneSAT); the MAPL-EMIT inference library from GitHub and model weights from Kaggle, run on EMIT granules fetched with earthaccess; pystac-client (Carbon Mapper); requests (IMEO).
- Statistics: numpy and scipy for v1; PyMC for the hierarchical version.
- Interface: Streamlit with a MapLibre or pydeck map; KMZ upload or coordinate paste; asset confirmation table; per-asset panel showing looks, detections, and the duty-factor posterior.
- Report: one HTML file per run with delivered intensity, the go or no-go recommendation, segment intensities, per-asset contributions, and every data source with retrieval date.

### 8b. Repo layout

```
methane-dd/
  config/sensors.yaml      # POD curves, quantification error, citations
  schemas/                 # asset, supplier-measurement, event-log schemas
  src/ingest/              # one module per source
  src/attribute.py
  src/model/               # duty factor, rate, sub-threshold, Monte Carlo
  src/score.py
  app/                     # Streamlit interface
  tests/                   # synthetic-truth tests
  cache/                   # raw API responses, dated
```

### 8c. Milestones

1. Area-of-interest loader (KMZ, coordinates, GeoJSON), asset discovery from OGIM, confirmation table, map view.
2. Detection ingest: MAPL-EMIT database, Carbon Mapper, EMIT L2B, IMEO; normalised plume table.
3. Observation ingest: MAPL-EMIT inference over all EMIT granules in the area; Carbon Mapper scenes; look table with cloud and wind.
4. Rate estimation for plumes without one; attribution with source locations and OGIM neighbours.
5. Duty-factor and rate model; per-asset posteriors.
6. Monte Carlo roll-up; segment and delivered intensity; grades; HTML report with go or no-go.
7. Denominators: state production, pipeline throughput, customer delivered volume.
8. Distribution segment from Subpart W and PHMSA; optional LNG segment.
9. Supplier-measurement and event-log ingestion.
10. Hierarchical model sharing information across assets, benchmarked against Carbon Mapper's.

### 8d. Tests that prove the statistics

- **Synthetic truth:** simulate assets with known p and Q, generate looks with the sensor POD curves, and check that 90% intervals contain the truth about 90% of the time across 1,000 simulations. This reuses the MRV OSSE sensor models.
- **Published reproduction:** reproduce a published persistence or basin result from the Carbon Mapper or Kairos literature within its stated uncertainty.
- **Hand-checked case:** one asset with 1 detection in 12 perfect looks must return the Beta(2, 12) posterior from our earlier worked example.

### 8e. Open questions before building

- Does IMEO's non-commercial licence permit use in paid customer work? If not, use IMEO only as a cross-check.
- Does the Carbon Mapper API expose scene footprints for all sensors, or only for some?
- Which published controlled-release results give usable POD curves for each sensor?
- What is the customer's actual contract path, and can each operator on it be reached for Section 3a?

## Sources

- [Carbon Mapper product guide](https://carbonmapper.org/articles/product-guide)
- [Carbon Mapper API documentation](https://api.carbonmapper.org/api/v1/docs)
- [EMIT L2B methane plume complexes V002, NASA Earthdata](https://www.earthdata.nasa.gov/data/catalog/lpcloud-emitl2bch4plm-002)
- [UNEP IMEO Eye on Methane downloads](https://methanedata.unep.org/download-dataset)
- [IMEO MARS data dictionary and licence](https://methanedata.unep.org/table_colum_deffinition_v1.pdf)
- [MethaneSAT datasets on Google Earth Engine](https://developers.google.com/earth-engine/datasets/publisher/edf-methanesat-ee)
- [MethaneSAT 2025 mission update](https://www.methanesat.org/project-updates/2025-was-year-highs-lows-and-hope-methanesat)

* [MAPL-EMIT paper, arXiv 2604.10094](https://arxiv.org/abs/2604.10094)
* [Google announcement of MAPL-EMIT](https://blog.google/innovation-and-ai/models-and-research/google-research/mapping-global-methane-emissions-from-space/)
* [Duren et al. 2025, The Carbon Mapper emissions monitoring system](https://amt.copernicus.org/articles/18/6933/2025/amt-18-6933-2025.pdf)
* [Carbon Mapper, IWGGMS 2026 presentation](https://atmosphere.copernicus.eu/sites/default/files/custom-uploads/IWGGMS%2022%20event/Day1/1.06_Duren.pdf)
* [Cusworth et al., super-emitter intensities, EGU 2026](https://meetingorganizer.copernicus.org/EGU26/EGU26-5831.html)
* [MethaneSAT basin intensities, ACP 2026](https://acp.copernicus.org/articles/26/5961/2026/)
* [US LNG supply chain measurement study (EEMDL)](https://www.cambridge.org/engage/chemrxiv/article-details/6882ca69fc5f0acb52e159e3)
* [EEMDL 2026 speakers](https://cvent.utexas.edu/event/17d21fcf-0071-4fd5-9e79-9cb0f71d8cb1/speakers) and [workshops](https://cvent.utexas.edu/event/17d21fcf-0071-4fd5-9e79-9cb0f71d8cb1/workshops)
