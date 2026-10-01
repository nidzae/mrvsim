# MRVSim Quick Start

MRVSim shows how well a set of methane sensors can measure a facility's yearly emissions, and what it costs. Everything here is a simulation: the facilities are synthetic, built to match published measurements of real US oil and gas basins. The tool's job is to tell you what a sensor mix *could* deliver, with a defensible error bar.

Open this guide any time with the **?** button in the top right.

---

## The two numbers the tool produces

For each facility:

1. **Methane intensity** — methane emitted as a percentage of the methane sold. Use this for gas purchasing.
2. **Absolute emissions** — tonnes of methane per year. Use this for oil-heavy facilities and for basin totals.

Each comes with a **90% interval**: a low and a high value. The tool is saying "the true number is between these, 9 times out of 10." A narrow interval means the sensors measured well. A wide one means they did not.

## The three colors on the map

You set a **bar** (for example, 0.2% intensity) and a **precision** (for example, ±30%). Each facility is then:

- **Green — Certified.** The high end of the interval is below the bar, and the interval is narrow enough.
- **Red — Fails.** The low end of the interval is above the bar.
- **Grey — Indeterminate.** The interval straddles the bar, or is too wide. More or better sensors are needed to decide.

Grey is the most common result with sparse monitoring. Turning grey into green or red is what the sensor controls are for.

## Five-minute walkthrough

1. **Load the default run.** Start the API (`.venv/bin/uvicorn mrvsim.api.server:app --port 8000`) and open the app. The **Sensors** panel on the left starts with the default mix (two aircraft passes per year, monthly GHGSat tasking on the top 30 % of facilities by throughput, TROPOMI everywhere). Press **Run**; the run selector in the top bar switches to it when it finishes.
2. **Set your bar.** In the top bar, choose the KPI (**intensity** or **absolute (t/yr)**), enter the **bar** and the **precision w**. The map recolors immediately; no rerun is needed.
3. **Click a grey facility.** The drill-down shows:
   - the interval as a bar against your bar line (thin band p5–p95, thick band p10–p90, circle at the median, triangle at the synthetic truth),
   - the **observation timeline**: one row per sensor, with detections, non-detections, cloud-outs, wind-outs and night passes,
   - **Compute variance budget**: which error source is making the interval wide (for example, "temporal sampling 60 %" means the sensors could not tell how often the source was on).
4. **Change the sensor mix.** In **Sensors**, tick a sensor class on or off and set coverage (share of facilities), surveys or taskings per year, and targeting (random or throughput-weighted). Try adding `cms_generic` at 20 % coverage with throughput targeting. Press **Run**. Interactive runs estimate a stratified subsample (10 facilities per stratum, 2,000 posterior draws, 3 replications; change these under **Advanced**) and take about a minute.
5. **Read the dashboard.** The headline tiles:
   - **Certified share** — percent of facilities, and of gas throughput, that are green.
   - **Calibration κ** — should sit between 0.85 and 0.95. If it is outside that band the tile turns red: the intervals are not trustworthy.
   - **Median interval width** — smaller is better.
   - **Completeness** — share of large emissions the sensors could see at all.
   - **Cost per tonne detected** and **cost per certified MMBtu**.
6. **Use the tornado chart.** On **Dashboard**, press **Compute tornado**. Each bar reruns the pipeline with one sensor change (a sensor off, doubled frequency, full coverage, or a missing sensor added at 20 %) and shows the change in median interval width. Negative bars narrow intervals; start with the most negative one.

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
- **Synthetic facilities.** Coordinates are representative, not real assets. Do not use a green dot to make a claim about a specific real operator.

## Glossary (short)

| Term | Meaning |
|---|---|
| Interval | The range the true value is 90% likely to be in |
| Calibration | Whether the intervals keep their 90% promise, checked on synthetic truth |
| Duty cycle | Fraction of the year a source is emitting |
| POD | Probability a sensor detects a source of a given size |
| Completeness | Share of large emissions the sensor system could detect at all |
| Tip-and-cue | A cheap sensor's detection triggers an expensive sensor's visit |
| Pareto frontier | The set of best trade-offs between cost and precision |

Full definitions are in the PRD glossary (`docs/PRD.md` §8). Interval and certification conventions: the interval shown is the two-sided 90 % credible interval; **Certified** uses the one-sided 90 % upper bound (p90) against the bar and the interval's relative half-width against the precision; **Fails** means the one-sided 90 % lower bound (p10) is above the bar.
