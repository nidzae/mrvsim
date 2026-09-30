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

1. **Load the default run.** It uses the default sensor mix (two aircraft surveys per year, monthly satellite tasking, no ground monitors) over a sample of US facilities.
2. **Set your bar.** In the top bar, enter an intensity bar and a precision. Watch the map recolor.
3. **Click a grey facility.** The drill-down shows:
   - the interval as a bar against your bar line,
   - a timeline of the year: when sensors looked, what they saw, and when clouds blocked them,
   - the **variance budget**: which error source is making the interval wide (for example, "temporal sampling 60%" means the sensors could not tell how often the source was on).
4. **Change the sensor mix.** Open **Sensors** on the left. Each sensor class has: on/off, coverage (share of facilities), frequency, and targeting. Try adding continuous ground monitors to the top 20% of facilities by throughput. Click **Run**. Under a minute later the map and dashboard update.
5. **Read the dashboard.** The headline numbers:
   - **Certified share** — percent of facilities, and of gas throughput, that are green.
   - **Calibration** — should sit between 0.85 and 0.95. If it is far below, the intervals are overconfident and the run is not trustworthy; the tool flags this.
   - **Median interval width** — smaller is better.
   - **Completeness** — share of large emissions the sensors could see at all.
   - **Cost per tonne detected** and **cost per certified MMBtu**.
6. **Use the tornado chart.** It shows which single change (more aircraft passes, more satellite tasking, more ground monitors) would narrow intervals the most. Start with the biggest bar.

## Finding the cheapest mix that meets a bar

Open **Optimize**. Enter the bar, the precision, and the share of throughput you want certified. Click **Find frontier**. The tool tries many sensor mixes and draws the **Pareto frontier**: cost on one axis, achievable precision on the other. Every point on the curve is a mix where you cannot get tighter intervals without spending more. Click a point to load that mix.

This chart is the answer to "what bar can be set today, with validated technology, at what cost."

## Asking Claude what is wrong

Open the **Gap analysis** panel. Claude has the current run's numbers. Ask things like:

- "Why is the Permian mostly grey?"
- "What is the cheapest way to get the Appalachian sample above 80% certified?"
- "Which error source dominates for gas-dominant well pads?"

Claude will explain and can propose a sensor configuration. Click **Apply** to load it, then **Run**.

## Things to know before you trust a number

- **Validation panel.** Shows whether the simulation reproduces published basin results (tests V1–V7). If any test is failing, treat outputs as provisional.
- **Validation tier.** Sensors are marked A through D based on whether their performance was measured in blind tests. By default only Tier A sensors count toward certification. You can relax this in **Sensors → Advanced**, and the map will flag facilities that depend on unvalidated sensors.
- **Attribution panel.** Lists every study, dataset, and method behind the current run. Entries marked "verify" have not yet been checked against the original publication.
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

Full definitions are in the PRD glossary (Help → Documentation).
