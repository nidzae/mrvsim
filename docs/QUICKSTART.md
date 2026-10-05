# MRVSim Quick Start

MRVSim shows how well a set of methane sensors can measure a facility's yearly emissions, and what it costs. Everything here is a simulation: the facilities are synthetic, built to match published measurements of real US oil and gas basins. The tool's job is to tell you what a sensor mix *could* deliver, with a defensible error bar.

Open this guide any time with the **?** button in the top right.

---

## The two numbers the tool produces

For each facility:

1. **Methane intensity** — methane emitted as a percentage of the methane sold. Use this for gas purchasing.
2. **Absolute emissions** — tonnes of methane per year. Use this for oil-heavy facilities and for basin totals.

Each comes with a **90% interval**: a low and a high value. The tool is saying "the true number is between these, 9 times out of 10." A narrow interval means the sensors measured well. A wide one means they did not.

## Two kinds of verdict

The selector at the top right, **verdicts: observations only / estimate**, changes what the colours and the dashboard mean. It needs no rerun.

- **Observations only** (the default). Only measurements count, and nothing is assumed about time nobody observed. A site is **certified** only if a continuous monitor measured it for the whole year and found it under the bar. It **fails** if the emissions actually measured already exceed the bar. Everything else is **undecided**. Aircraft and satellite looks are instants: they can flag a site but cannot certify it. Expect almost every dot to be grey unless you deploy continuous monitors. A single ordinary monitor network observes about 82 % of hours (outages, wind), which is not a full year. To reach one, set **networks per site** to 2 or more, or pick **cms_high_availability** (nodes in every wind sector, backup power, about three times the price); two of those observe most sites every hour. The dashboard then shows what that costs per certified facility.
- **Estimate.** Measurements are combined with published leak statistics for sites like this one, which fill in the unobserved time. This gives many more verdicts, but some rest on those statistics more than on measurements.

The difference between the two views is how much of a verdict rests on assumption. The facility panel shows both.

## The three colors on the map

You set a **bar** (for example, 0.2% intensity). Each facility is then decided at 95 % confidence:

- **Green — Certified.** The high end of the interval (p95) is below the bar: at least a 95 % chance the facility is below the bar.
- **Red — Fails.** The low end of the interval (p5) is above the bar: at least a 95 % chance it is above.
- **Grey — Indeterminate.** The interval straddles the bar. More or better sensors are needed to decide.

Two more things are shown on top of the colour, and neither changes the decision:

- **Dark ring — wide.** The interval's relative half-width w is above your **precision w_max** (for example 0.3). The facility is decided, but its number is imprecise.
- **Faded — prior-only.** The facility is certified, but the observations barely narrowed what the population prior already said. The certification rests on the population, not on this site.

Grey is the most common result with sparse monitoring. Turning grey into green or red, and faded into solid, is what the sensor controls are for.

## Five-minute walkthrough

1. **Load the default run.** Start the API (`.venv/bin/uvicorn mrvsim.api.server:app --port 8000`) and open the app. The **Sensors** panel on the left starts with the default mix (two aircraft passes per year, monthly GHGSat tasking on the top 30 % of facilities by throughput, TROPOMI everywhere). Press **Run**; the run selector in the top bar switches to it when it finishes.
2. **Set your bar.** In the top bar, choose the KPI (**intensity** or **absolute (t/yr)**), enter the **bar** and the **precision w_max** (grade only). The map recolors immediately; no rerun is needed. **Hover a dot** to see both KPIs at once (intensity and absolute emissions, each as median and 90 % interval) with the state, precision and prior-only flag; click it for the full panel.
3. **Click a grey facility.** The drill-down opens on the right; drag its left edge to make it wider (the width is remembered). It shows the selected KPI first and the other KPI below it, each decided at its own bar:
   - the **posterior probability of being below the bar** (for example "97 %") and the decision it implies, following the top-bar controls like the map,
   - the interval as a bar against your bar line (faint dashed band: the prior with no data; thin band p5–p95; thick band p10–p90; circle at the median; triangle at the synthetic truth),
   - **Monitoring at this facility**: every sensor in the policy, its rule, whether this site was selected and why (for throughput targeting, its rank by gas throughput), and the planned visit days,
   - the **precision** (w against w_max) and the **evidence** (usable snapshots, survey visits, CMS hours, and how much narrower the posterior is than the prior; "prior-only" is flagged in red),
   - the **observation timeline**: one row per sensor, with detections, non-detections, cloud-outs, wind-outs and night passes; lighter markers are **incidental** looks, when this site fell inside a scene a satellite framed on a neighbour (GHGSat images 12 km × 12 km) or inside an aircraft's survey block. They count as evidence and cost nothing,
   - **Compute variance budget**: which error source is making the interval wide (for example, "temporal sampling 60 %" means the sensors could not tell how often the source was on).
4. **Pick a compute mode.** Under the sensor list, **quick** (default) estimates 10 facilities per stratum with 2,000 posterior draws and 3 replications in about a minute; **full** estimates every facility of the default sample with 10,000 draws and 5 replications and takes tens of minutes; **custom** uses the Advanced settings. The panel shows the time estimate. Explore with quick runs; when a mix looks good, open **Dashboard** and press **Re-run this mix at full resolution**.
5. **Change the sensor mix.** In **Sensors**, tick a sensor class on or off and set coverage (share of facilities), surveys or taskings per year, and targeting (random, or the top facilities by gas throughput). For aircraft, drone and OGI you can also choose **scheduling**: independent dates per facility, or a **regional campaign** that flies each basin within a window of N days so neighbouring sites are observed together (the default for Bridger). Sensors are assigned per facility by these rules; a site's location never decides which sensors it is *assigned*, but a neighbour's tasked scene can still capture it (**incidental capture**, on by default under Advanced). Try adding `cms_generic` at 20 % coverage with throughput targeting. Press **Run**. Interactive runs estimate a stratified subsample (10 facilities per stratum, 2,000 posterior draws, 3 replications; change these under **Advanced**) and take about a minute.
6. **Read the dashboard.** The headline tiles:
   - **Certified share** — percent of facilities, and of gas throughput, that are green; the sub-line says how many of those are also precise and how many are prior-only.
   - **Calibration κ** — should sit between 0.85 and 0.95. If it is outside that band the tile turns red: the intervals are not trustworthy.
   - **Median interval width** — smaller is better.
   - **Emissions with a decided outcome** — of all the methane actually emitted, the share that comes from sites where the monitoring reached a verdict (certified or fails). The rest sits at indeterminate sites.
   - **Cost per tonne detected** and **cost per certified MMBtu**.
7. **Use the tornado chart.** On **Dashboard**, press **Compute tornado**. Each bar reruns the pipeline with one sensor change (a sensor off, doubled frequency, full coverage, or a missing sensor added at 20 %) and shows the change in median interval width. Negative bars narrow intervals; start with the most negative one.

## Finding the cheapest mix that meets a bar

Open **Optimize**. Enter the precision **w_max**, the share of throughput you want certified (**θ**), and the number of trials. Click **Find frontier**. The tool tries sensor mixes with Optuna and lists the **Pareto set**: mixes where you cannot get tighter intervals without spending more. The frontier chart (cost vs. median w) appears on the **Dashboard**. Press **load** on a row to put that mix into **Sensors**, then **Run**.

This chart is the answer to "what bar can be set today, with validated technology, at what cost."

## Asking Claude what is wrong

Open the **Gap analysis** tab. Claude has the current run's headline metrics, slice tables, and tornado chart (if computed) in context. The API server supplies the Anthropic credentials (`ANTHROPIC_API_KEY` or `ant auth login` on the machine running it); nothing is stored in the browser. Ask things like:

- "Why is the Permian mostly grey?"
- "What is the cheapest way to get the Appalachian sample above 80% certified?"
- "Which error source dominates for gas-dominant well pads?"

Claude will explain and can propose a sensor configuration as a small YAML block. Click **Apply** to load it into **Sensors**, then **Run**.

## Things to know before you trust a number

- **Validation tab.** Shows whether the simulation reproduces published basin results (tests V1–V7) with the compared values. While the priors are PLACEHOLDER the tests refuse to run as validation; a "diagnostic only" run exercises the machinery but proves nothing about published data. Treat outputs as provisional until V1–V7 pass on fitted priors.
- **Validation tier.** Sensors are marked A through D based on whether their performance was measured in blind tests. By default only Tier A sensors count toward certification and only Tier A sensors are listed. You can relax the tier filter in **Sensors → Advanced**; Tier B–D sensors show a yellow tier badge.
- **Attribution tab.** Lists every study, dataset, and method behind the current run, the provenance of the priors and stratum weights, and which sensor parameter blocks are not yet fitted to blind-test data. Entries marked "verify" have not yet been checked against the original publication.
- **Satellites follow real orbits, with simplifications.** Each satellite's published orbit is propagated through the year, and a site is seen only when the satellite passes overhead in daylight under a clear enough sky, within the instrument's real swath or pointing reach. Three simplifications: each instrument is one satellite even where a constellation exists, so revisit is on the low side; pass dates are representative of the orbit, not the actual historical dates; and pointable satellites (GHGSat, Tanager, PRISMA, EnMAP) have no limit on how many sites they can be tasked on, which is on the generous side. The sensor mix that loads by default is an illustration, not a description of what is monitored today.
- **Real sites, simulated emissions.** Each well-pad dot sits on a real production site and carries that site's 2022 oil and gas production (OGIM v3.0). Its leaks are simulated from basin statistics, not measured there. Do not use a dot's colour to make a claim about a specific real operator. Compressor stations, processing plants and storage sites use real locations with simulated throughput.
- **Leaks depend on what is on the site.** A site's simulated leaks follow from its number of wells, whether it is an oil or gas site, and how much each well produces, using published equipment-level leak measurements. More wells and more productive wells mean more and larger leaks; small sites leak less in total but far more per unit of gas. The facility panel lists these inputs. Rare very large leaks seen by aircraft surveys are added on top for sites that produce enough gas to sustain one, with a per-well chance fitted per basin to published survey data.
- **The dots are a poll, and large sites are deliberately over-represented.** The US has about 577,000 producing sites in the data; the map shows about 2,200 (quick) or 7,200 (full). Because a few large sites hold most of the gas, the poll includes far more of them than their numbers warrant, then counts each dot by how many real sites, or how much real gas, it stands for. So the map shows more big sites than reality (about a quarter of well-pad dots are small sites, against three quarters in reality), but every headline number is corrected for this. Hover a dot to see its production and how many real sites it stands for. The brown shading behind the dots is the density of all real sites (untick **all … real sites** in the legend to hide it). A sensor's **coverage** is a share of real facilities: "top 30 % by throughput" means the largest sites that together make up 30 % of real sites.

## Glossary (short)

| Term | Meaning |
|---|---|
| Interval | The range the true value is 90% likely to be in |
| Calibration | Whether the intervals keep their 90% promise, checked on synthetic truth |
| Duty cycle | Fraction of the year a source is emitting |
| POD | Probability a sensor detects a source of a given size |
| Emissions with a decided outcome | Share of all emitted methane that comes from sites with a verdict (certified or fails) rather than indeterminate |
| Tip-and-cue | A cheap sensor's detection triggers an expensive sensor's visit |
| Pareto frontier | The set of best trade-offs between cost and precision |

Full definitions are in the PRD glossary (`docs/PRD.md` §8). Interval and certification conventions: the interval shown is the two-sided 90 % credible interval; **Certified** means its upper end (p95) is at or below the bar; **Fails** means its lower end (p5) is above the bar; **Indeterminate** means it straddles the bar. Precision (w against w_max) and evidence (posterior width as a fraction of the prior's) are shown alongside and do not change the decision.
