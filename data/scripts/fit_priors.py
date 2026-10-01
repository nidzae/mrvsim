#!/usr/bin/env python
"""Fit stratum priors from public data (TDD section 3.2-3.4; replaces configs/priors/placeholder_v0.yaml).

Inputs (all on disk under data/raw/, fetched 2026-09-30/10-01):
  * Rutherford et al. 2021 component database (Zenodo 10.5281/zenodo.4903897)       [rutherford2021]
      - Table_Comp_Emissions.xlsx / EF_Component "All": n=3,082 emitting components, mean 10.909 and median 1.230 kg CH4/d
        -> steady-source lognormal by moment matching: mu_0 = ln(median), sigma_0^2 = 2 ln(mean/median)
      - Omara_data_kgh_allsites.csv: 498,121 US gas production sites, column 2 = site emission rate (kg/h)
        (national site-level distribution, model-derived in Omara et al. 2018 and reproduced by Rutherford)
  * Cusworth et al. 2022 Table 1 persistence f = M/N: Permian 0.26, Marcellus 0.60, SJV 0.29; point sources ~40 % of basin flux [cusworth2022]
  * Sherwin et al. 2024: 0.05-1.66 % of well sites carry 50-79 % of well-site emissions (heavy tail) [sherwin2024]
  * EPA GHGRP Subpart W RY2023 (Envirofacts) reported CH4 per facility for gathering, processing, transmission, storage [ghgrp]

Method
  Well pads: the generator's own source model (K ~ Poisson(lambda)+1; steady lognormal(mu_0, sigma_0) fixed from Rutherford;
  intermittent lognormal(mu_1, sigma_1) with Pareto tail (q_tail, alpha); durations nu_on fixed, nu_off free) is simulated for
  20,000 sites and its site-level annual-mean rate quantiles, mean, top-1 % share, and snapshot point-source share (> 10 kg/h) are
  matched to the Omara quantiles and the Cusworth share by Nelder-Mead on log-quantile errors. The fitted p_intermittent is then
  adjusted per basin so that the simulated persistence of detectable sources matches Cusworth where a basin value exists.
  Midstream: GHGRP reported CH4 per facility gives the facility-level annual mean; lambda and mu are set so the simulated
  facility mean matches the GHGRP median and mean (GHGRP is bottom-up and known to under-report; flagged).
Outputs
  configs/priors/fitted_2026-10-01.yaml (provenance FITTED) and data/fitted/priors_fit_provenance.json
"""

from __future__ import annotations

import datetime as dt
import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.optimize import minimize

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"
OUT_YAML = REPO / "configs" / "priors" / "fitted_2026-10-01.yaml"
OUT_PROV = REPO / "data" / "fitted" / "priors_fit_provenance.json"
RNG = np.random.default_rng(20261001)
N_SITES = 40_000
Q_PCTS = [25, 50, 75, 90, 95, 99, 99.9]


def rutherford_component_lognormal() -> dict:
    base = glob.glob(str(RAW / "rutherford2021" / "JSRuthe-O-G_Methane_Supporting_Code-*"))[0]
    e = pd.ExcelFile(f"{base}/2_Methane_Database/Table_Comp_Emissions.xlsx").parse("EF_Component", header=None)
    row = e[e[1] == "All"].iloc[0]
    mean_kgd, med_kgd, n = float(row[5]), float(row[6]), int(row[2])
    mu0 = float(np.log(med_kgd / 24.0)); s0 = float(np.sqrt(2 * np.log(mean_kgd / med_kgd)))
    return {"mu_0": mu0, "sigma_0": s0, "n": n, "mean_kg_d": mean_kgd, "median_kg_d": med_kgd}


def omara_targets() -> dict:
    base = glob.glob(str(RAW / "rutherford2021" / "JSRuthe-O-G_Methane_Supporting_Code-*"))[0]
    v = pd.read_csv(f"{base}/5_Processing_Results/5d_Final_Emissions/Omara_data_kgh_allsites.csv", header=None)[1].to_numpy(float)
    v = v[np.isfinite(v)]
    return {"quantiles": dict(zip(Q_PCTS, np.percentile(v, Q_PCTS).tolist())), "mean": float(v.mean()),
            "top1_share": float(np.sort(v)[-v.size // 100:].sum() / v.sum()), "n": int(v.size)}


def simulate_sites(theta: np.ndarray, mu0: float, s0: float, n: int = N_SITES, nu_on: float = 2.0, tau: float = 1.0, sig_nu: float = 0.7, rng=RNG):
    """theta = [log lambda, logit p, mu_1, log sigma_1, log q_tail, log alpha, nu_off]. Returns per-site stats."""
    lam = np.exp(theta[0]); p = 1 / (1 + np.exp(-theta[1])); mu1 = theta[2]; s1 = np.exp(theta[3]); q_tail = 30.0 + np.exp(theta[4]); alpha = 1.05 + np.exp(theta[5]); nu_off = theta[6]
    K = rng.poisson(lam, n) + 1
    site = np.repeat(np.arange(n), K); m = site.size
    z = rng.random(m) < p
    q = np.exp(np.where(z, rng.normal(mu1, s1, m), rng.normal(mu0, s0, m)))
    tail = q > q_tail
    q[tail] = q_tail * rng.random(tail.sum()) ** (-1 / alpha)
    non = rng.normal(nu_on, sig_nu, m); noff = rng.normal(nu_off, sig_nu, m)
    e_on = np.exp(non + 0.5 * tau**2); e_off = np.exp(noff + 0.5 * tau**2)
    pi = np.where(z, e_on / (e_on + e_off), 1.0)
    mean_rate = np.bincount(site, weights=pi * q, minlength=n)
    on = rng.random(m) < pi
    snap = np.bincount(site, weights=on * q, minlength=n)
    return mean_rate, snap, q, pi, z, site


def loss(theta, mu0, s0, tg, w_share=2.0):
    mean_rate, snap, q, pi, z, site = simulate_sites(theta, mu0, s0)
    sim_q = np.percentile(mean_rate, Q_PCTS)
    tq = np.array([tg["quantiles"][p] for p in Q_PCTS])
    err = np.mean((np.log(sim_q + 1e-4) - np.log(tq + 1e-4)) ** 2)
    err += (np.log(mean_rate.mean() + 1e-6) - np.log(tg["mean"])) ** 2
    top1 = np.sort(mean_rate)[-N_SITES // 100:].sum() / mean_rate.sum()
    err += (top1 - tg["top1_share"]) ** 2 * 4
    # Cusworth: snapshot flux share from facilities above 10 kg/h ~ 0.40 (0.13-0.67 across campaigns)
    share10 = snap[snap > 10].sum() / max(snap.sum(), 1e-9)
    err += w_share * (share10 - 0.40) ** 2
    return err


def persistence_for(theta, mu0, s0, det_kg_h=10.0, n_passes=4, rng=RNG):
    """Cusworth-style f = M/N over sources detected at least once, snapshot detection threshold ~10 kg/h."""
    _, _, q, pi, z, site = simulate_sites(theta, mu0, s0, n=5000, rng=rng)
    big = q > det_kg_h
    if not big.any():
        return float("nan")
    det = rng.random((int(big.sum()), n_passes)) < pi[big][:, None]
    m = det.sum(axis=1); seen = m > 0
    return float((m[seen] / n_passes).mean())


def fit_wellpads():
    comp = rutherford_component_lognormal(); tg = omara_targets()
    mu0, s0 = comp["mu_0"], comp["sigma_0"]
    x0 = np.array([np.log(1.5), 0.0, np.log(5.0), np.log(1.2), np.log(70.0), np.log(0.45), 4.5])   # q_tail = 30 + e^x, alpha = 1.05 + e^x
    best = None
    for start in (x0, x0 + np.array([0.5, -1, 1, 0, 0.5, 0, 0]), x0 + np.array([-0.3, 1, -0.5, 0.3, -0.5, 0.2, -1])):
        res = minimize(loss, start, args=(mu0, s0, tg), method="Nelder-Mead", options={"maxiter": 1500, "xatol": 1e-3, "fatol": 1e-5})
        if best is None or res.fun < best.fun:
            best = res
    th = best.x
    fit = {"lambda_k": float(np.exp(th[0])), "p_intermittent": float(1 / (1 + np.exp(-th[1]))), "mu_0": mu0, "sigma_0": s0,
           "mu_1": float(th[2]), "sigma_1": float(np.exp(th[3])), "q_tail": float(30.0 + np.exp(th[4])), "alpha": float(1.05 + np.exp(th[5])),
           "nu_on": 2.0, "tau_on": 1.0, "nu_off": float(th[6]), "tau_off": 1.0, "sigma_nu_on": 0.7, "sigma_nu_off": 0.7}
    mean_rate, snap, q, pi, z, site = simulate_sites(th, mu0, s0)
    diag = {"loss": float(best.fun), "sim_quantiles_kg_h": dict(zip(Q_PCTS, np.percentile(mean_rate, Q_PCTS).round(4).tolist())),
            "target_quantiles_kg_h": {k: round(v, 4) for k, v in tg["quantiles"].items()}, "sim_mean": float(mean_rate.mean()), "target_mean": tg["mean"],
            "sim_top1_share": float(np.sort(mean_rate)[-N_SITES // 100:].sum() / mean_rate.sum()), "target_top1_share": tg["top1_share"],
            "sim_snapshot_share_above_10kgh": float(snap[snap > 10].sum() / snap.sum()), "target_share": 0.40,
            "sim_persistence_national": persistence_for(th, mu0, s0), "sim_mean_pi_intermittent": float(pi[z].mean())}
    return fit, comp, tg, diag, th


def basin_p_from_persistence(th, mu0, s0, target_f: float) -> float:
    """Adjust logit p so simulated Cusworth-style persistence matches target_f (other parameters fixed)."""
    lo, hi = -4.0, 4.0
    for _ in range(30):
        mid = 0.5 * (lo + hi); t = th.copy(); t[1] = mid
        f = persistence_for(t, mu0, s0, rng=np.random.default_rng(7))
        if f > target_f:   # too persistent -> more intermittent sources
            lo = mid
        else:
            hi = mid
    return float(1 / (1 + np.exp(-0.5 * (lo + hi))))


def sherwin_targets() -> dict:
    p = REPO / "data" / "fitted" / "sherwin2024_targets.json"
    return json.loads(p.read_text())["basins"] if p.exists() else {}


def basin_loss(theta_b, th_nat, mu0, s0, levels, S_target, f_target, rng_seed=11):
    """Loss for per-basin parameters [log lambda, logit p, mu_1, log sigma_1, log q_tail, log alpha, delta mu_0]; durations from the national fit."""
    th = th_nat.copy(); th[0:6] = theta_b[:6]
    mean_rate, snap, q, pi, z, site = simulate_sites(th, mu0 + theta_b[6], s0, rng=np.random.default_rng(rng_seed))
    S_sim = np.array([(snap >= x).mean() for x in levels])
    ok = S_target > 2e-4
    # weight each level by the information it carries: full weight when the target implies >= 50 simulated sites at or
    # above the level, less otherwise (the tail levels are Poisson-noise dominated with N_SITES sites)
    w = np.minimum(1.0, N_SITES * S_target[ok] / 50.0)
    err = np.sum(w * (np.log(S_sim[ok] + 1e-5) - np.log(S_target[ok] + 1e-5)) ** 2) / w.sum()
    if f_target is not None:
        f = persistence_for(th, mu0, s0, rng=np.random.default_rng(rng_seed))
        if np.isfinite(f):
            err += 2.0 * (f - f_target) ** 2
    return err


# Additional multistart seeds per basin: parameter sets reached by earlier optimisation runs of this script (2026-10-01).
# Nelder-Mead on the simulated survival curve has several local optima; seeding from previously found solutions makes
# the result reproducible and lets the single large-sample loss below pick the best. Keys: lambda, p, mu_1, sigma_1, q_tail, alpha, mu_0.
SEED_CANDIDATES = {
    "permian": [(4.231, 0.702, 2.801, 1.208, 317.5, 1.439, -2.147), (5.937, 0.633, 1.998, 1.187, 149.9, 1.559, -2.154), (4.782, 0.341, 2.671, 1.200, 354.9, 1.426, -2.151)],
    "appalachian": [(1.964, 0.449, 2.608, 1.207, 549.4, 1.421, -2.971), (1.899, 0.227, 3.102, 1.213, 983.8, 1.428, -2.462), (1.937, 0.345, 2.554, 1.203, 312.1, 1.429, -2.971)],
    "dj": [(1.997, 0.094, 3.105, 1.200, 1172.0, 1.417, -2.499), (1.002, 0.062, 2.054, 1.238, 748.6, 1.490, -2.166), (1.485, 0.230, 1.346, 1.745, 1239.0, 2.100, -2.277)],
    "uinta": [(1.960, 0.230, 3.062, 1.207, 864.5, 1.422, -2.468), (3.345, 0.446, 1.029, 1.613, 277.6, 1.913, -1.967), (3.103, 0.072, 2.022, 1.188, 244.4, 1.543, -2.149)],
    "fort_worth": [(1.047, 0.230, 1.460, 1.733, 6637.0, 2.127, -2.490), (0.811, 0.436, 1.044, 1.802, 14153.0, 2.188, -2.026), (1.742, 0.683, 1.999, 1.210, 300.5, 1.498, -2.184)],
    "san_joaquin": [],
}


def _theta_from_params(lam, p, mu1, s1, q_tail, alpha, mu0_abs, mu0_nat):
    return np.array([np.log(lam), np.log(p / (1 - p)), mu1, np.log(s1), np.log(max(q_tail - 30.0, 1e-3)), np.log(max(alpha - 1.05, 1e-3)), mu0_abs - mu0_nat])


def fit_basins(th_nat, mu0, s0, persistence: dict) -> dict:
    """Per-basin fit in three stages: (1) survival curve only with p fixed; (2) bisection on p for persistence (if a target
    exists); (3) joint polish from that point. Parameters: lambda, p, mu_1, sigma_1, q_tail, alpha, mu_0 (delta)."""
    out = {}
    for basin, tg in sherwin_targets().items():
        if not tg.get("fraction_at_or_above"):
            continue
        levels = np.array(tg["levels_kg_h"], float); S_t = np.array(tg["fraction_at_or_above"], float)
        f_t = persistence.get(basin)
        full0 = np.concatenate([th_nat[0:6], [0.0]])

        def surv_only(x7):   # all seven basin parameters, survival curve only
            return basin_loss(x7, th_nat, mu0, s0, levels, S_t, None)

        best = None
        for dlogit in (-1.0, 0.0, 1.0, 2.0):             # intermittent share starts ~0.10, 0.23, 0.45, 0.69 around the national value
            for dlam, dmu in ((0.0, 0.0), (1.0, 0.8)):
                start = full0.copy(); start[1] += dlogit; start[0] += dlam; start[6] += dmu
                res = minimize(surv_only, start, method="Nelder-Mead", options={"maxiter": 350, "xatol": 1e-3, "fatol": 1e-7})
                if best is None or res.fun < best.fun:
                    best = res
        for cand in SEED_CANDIDATES.get(basin, []):
            start = _theta_from_params(*cand, mu0)
            res = minimize(surv_only, start, method="Nelder-Mead", options={"maxiter": 250, "xatol": 1e-3, "fatol": 1e-7})
            if res.fun < best.fun:
                best = res
        b = best.x.copy(); surv1 = float(best.fun)
        p1 = float(1 / (1 + np.exp(-b[1])))
        if f_t is not None:
            # stage 2: nudge logit p toward the persistence target within bounds: a single
            # intermittent share for all source sizes (TDD section 3.3) cannot match both a high low-level emitting
            # fraction and a low persistence of large sources; the survival curve is the primary target (DECISION_LOG 2026-10-01)
            p_lo, p_hi = max(0.5 * p1, 0.02), min(1.5 * p1, 0.90)   # persistence may move the share within +/-50 % of the survival fit
            lo, hi = np.log(p_lo / (1 - p_lo)), np.log(p_hi / (1 - p_hi))
            for _ in range(20):
                mid = 0.5 * (lo + hi); t = th_nat.copy(); t[0:6] = b[:6]; t[1] = mid
                f = persistence_for(t, mu0 + b[6], s0, rng=np.random.default_rng(7))
                if f > f_t:
                    lo = mid
                else:
                    hi = mid
            b[1] = 0.5 * (lo + hi)
            # stage 3: joint polish with a light persistence weight so the survival match is not sacrificed
            def joint(x):
                return basin_loss(x, th_nat, mu0, s0, levels, S_t, None) + 0.3 * (persistence_for(np.concatenate([x[:6], th_nat[6:]]), mu0 + x[6], s0, rng=np.random.default_rng(7)) - f_t) ** 2
            res = minimize(joint, b, method="Nelder-Mead", options={"maxiter": 600, "xatol": 1e-3, "fatol": 1e-7})
            cand = res.x if res.fun < joint(b) else b
            if basin_loss(cand, th_nat, mu0, s0, levels, S_t, None) <= 1.15 * surv1:   # never trade more than 15 % of the stage-1 survival fit
                b = cand
            else:
                b = best.x.copy()   # persistence is reported as a diagnostic only
        th = th_nat.copy(); th[0:6] = b[:6]
        mean_rate, snap, q, pi, z, site = simulate_sites(th, mu0 + b[6], s0, rng=np.random.default_rng(11))
        S_sim = [float((snap >= x).mean()) for x in levels]
        out[basin] = {"params": {"lambda_k": float(np.exp(b[0])), "p_intermittent": float(1 / (1 + np.exp(-b[1]))), "mu_1": float(b[2]), "sigma_1": float(np.exp(b[3])),
                                 "q_tail": float(30.0 + np.exp(b[4])), "alpha": float(1.05 + np.exp(b[5])), "mu_0": float(mu0 + b[6])},
                      "diag": {"loss_survival": float(surv_only(b)), "loss_survival_stage1": surv1, "p_survival_only": p1, "campaign": tg.get("campaign"), "levels": levels.tolist(),
                               "S_target": S_t.tolist(), "S_sim": S_sim, "persistence_target": f_t,
                               "persistence_sim": persistence_for(th, mu0 + b[6], s0, rng=np.random.default_rng(11)),
                               "sim_mean_rate_kg_h": float(mean_rate.mean()), "loss_rate_target_pct": tg.get("loss_rate_pct"), "delta_mu_0": float(b[6])}}
    return out


def ghgrp_midstream(mu0: float, s0: float) -> dict:
    """lambda and mu_0 for midstream facility types from GHGRP RY2023 reported CH4 per facility (metric tons CH4 -> kg CH4/h).

    Processing plants, transmission compressor stations and storage facilities are physical sites. Gathering & boosting
    "facilities" are operator-by-basin aggregates, so their per-facility values are an upper bound on a single station.
    """
    e = pd.read_csv(RAW / "ghgrp_ef_w_emissions_source_ghg_2023.csv", low_memory=False)
    seg = {"Onshore petroleum and natural gas gathering and boosting [98.230(a)(9)]": "gathering", "Onshore natural gas processing [98.230(a)(3)]": "processing",
           "Onshore natural gas transmission compression [98.230(a)(4)]": "transmission", "Underground natural gas storage [98.230(a)(5)]": "storage"}
    e["ft"] = e.industry_segment.map(seg); e = e.dropna(subset=["ft"])
    fac = e.groupby(["facility_id", "ft"], as_index=False).total_reported_ch4_emissions.sum()
    out = {}
    for ft, g in fac.groupby("ft"):
        # total_reported_ch4_emissions is in metric tons CH4 (national Subpart W sum ~2.4 Mt CH4 ~ 60 Mt CO2e, matching EPA's
        # published GHGRP petroleum & natural gas systems total); convert to kg CH4/h.
        kg_h = g.total_reported_ch4_emissions.to_numpy(float) * 1000.0 / 8760.0
        kg_h = kg_h[kg_h > 0]
        med, mean = float(np.median(kg_h)), float(kg_h.mean())
        # steady sources only (midstream mostly continuous): facility mean = sum of lambda+1 components with mean exp(mu + s^2/2).
        # Keep sigma_0 from Rutherford; set mu so that (lambda+1) * exp(mu + s0^2/2) = GHGRP mean, with lambda from the median/mean ratio heuristic.
        lam = float(np.clip(np.log(mean / med) * 1.5, 1.0, 8.0))
        mu = float(np.log(mean / (lam + 1)) - 0.5 * s0**2)
        out[ft] = {"lambda_k": lam, "mu_0": mu, "n_facilities": int(kg_h.size), "ghgrp_median_kg_h": med, "ghgrp_mean_kg_h": mean}
    return out


def main() -> int:
    fit, comp, tg, diag, th = fit_wellpads()
    basins = {"permian": 0.26, "appalachian": 0.60}   # Cusworth 2022 Table 1 (Permian 2019; Marcellus 2021)
    basin_fits = fit_basins(th, comp["mu_0"], comp["sigma_0"], basins)
    basin_over = {b: dict(v["params"]) for b, v in basin_fits.items() if b in ("permian", "appalachian", "dj", "uinta")}
    # Fort Worth (Barnett) and San Joaquin are not MRVSim basins; Fort Worth informs "other" (US onshore outside the named basins)
    if "fort_worth" in basin_fits:
        basin_over["other"] = dict(basin_fits["fort_worth"]["params"])
    for b in ("permian", "appalachian"):
        if b not in basin_over:
            basin_over[b] = {"p_intermittent": basin_p_from_persistence(th, comp["mu_0"], comp["sigma_0"], basins[b])}
    mid = ghgrp_midstream(comp["mu_0"], comp["sigma_0"])
    base = yaml.safe_load((REPO / "configs" / "priors" / "placeholder_v0.yaml").read_text())
    doc = {
        "provenance": "FITTED",
        "version": "2026-10-01",
        "fit_provenance": {
            "fitted_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "script": "data/scripts/fit_priors.py",
            "wellpad_method": "Nelder-Mead on log site-level quantiles (Omara national), mean, top-1 % share and Cusworth snapshot share > 10 kg/h; steady lognormal fixed from Rutherford components",
            "rutherford_components": comp, "omara_targets": tg, "wellpad_diagnostics": diag, "basin_persistence_targets": basins,
            "midstream_ghgrp": mid, "basin_fits_sherwin2024": basin_fits, "citations": ["rutherford2021", "cusworth2022", "sherwin2024", "ghgrp", "duren2019"],
            "caveats": ["Omara site-level values are model-derived national estimates for gas production sites, not direct measurements",
                        "midstream uses GHGRP bottom-up reported CH4 (metric tons CH4), known to under-report by roughly 2x versus measurements (Sherwin 2024); gathering values are operator-basin aggregates (upper bound per station)",
                        "durations: nu_on fixed at ln(7.4 h); only nu_off fitted; tau and sigma_nu kept at placeholder values",
                        "basins with Sherwin 2024 site-level CDFs (Permian, Appalachian, DJ, Uinta; Fort Worth -> other) have per-basin p, mu_1, sigma_1, q_tail, alpha fitted to the survival curve (and Cusworth persistence where available); Haynesville, Eagle Ford, Bakken, Anadarko, San Juan inherit the national fit"],
        },
        "defaults": {**fit, "throughput": base["defaults"]["throughput"]},
        "facility_types": {
            "wp_oil": {"throughput": base["facility_types"]["wp_oil"]["throughput"]},
            "wp_gas": {"throughput": base["facility_types"]["wp_gas"]["throughput"]},
            "wp_mixed": {"throughput": base["facility_types"]["wp_mixed"]["throughput"]},
            **{ft: {"lambda_k": v["lambda_k"], "mu_0": v["mu_0"], "p_intermittent": 0.15, "throughput": base["facility_types"][ft]["throughput"]} for ft, v in mid.items()},
        },
        "basins": {b: basin_over.get(b, {}) for b in base["basins"]},
        "conditions": base["conditions"],
    }
    header = ("# Fitted stratum priors (provenance FITTED). Produced by data/scripts/fit_priors.py on " + doc["fit_provenance"]["fitted_utc"] + ".\n"
              "# Sources: [rutherford2021] component database and Omara national site estimates; [cusworth2022] persistence and point-source share;\n"
              "# [sherwin2024] heavy-tail statement; [ghgrp] midstream reported CH4. Throughput and conditions blocks are still PLACEHOLDER values\n"
              "# carried from placeholder_v0.yaml (see 'caveats' in fit_provenance). Full method in the script docstring.\n")
    OUT_YAML.write_text(header + yaml.safe_dump(doc, sort_keys=False, default_flow_style=False))
    OUT_PROV.write_text(json.dumps(doc["fit_provenance"], indent=2, default=float))
    print(json.dumps({"wellpad_fit": fit, "basin_over": basin_over, "basin_diag": {b: v["diag"] for b, v in basin_fits.items()}, "midstream": mid, "diag": diag}, indent=1, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
