"""What the estimator is allowed to see (TDD section 6, first paragraph).

:class:`EstimatorInputs` is built from an :class:`~mrvsim.observe.simulator.ObservationSet`,
the sensor library, and the *non-truth* facility attributes (stratum, surface
reflectance, GHGRP flag, energy allocation f_gas) plus a noisy denominator
G_hat = G exp(eta) (TDD section 6.6). It never touches S_ij(t), q_ij, K_i, or the
oracle columns of the log.

Snapshot non-detections are grouped by (sensor, wind bin) per facility so the
likelihood evaluates each distinct condition once and raises it to the count
(DECISION_LOG 2026-09-30, Phase 4: 0.5 m/s wind bins are a speed approximation).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from mrvsim.io.seeds import SeedTree
from mrvsim.observe.simulator import CMSLog, ObservationLog, ObservationSet
from mrvsim.population.generate import Population
from mrvsim.sensors.library import SensorLibrary

WIND_BIN_M_S = 0.5
SIGMA_G_GHGRP = 0.05      # TDD section 6.6
SIGMA_G_OTHER = 0.15


@dataclass
class SnapshotGroups:
    """Non-detections grouped by (facility, sensor, wind bin); detections kept individually."""

    # ND groups (CSR by facility)
    nd_offset: np.ndarray            # (n_fac + 1)
    nd_sensor: np.ndarray            # (n_groups,) index into sensor_keys
    nd_wind: np.ndarray              # (n_groups,) bin-centre wind, m/s
    nd_count: np.ndarray             # (n_groups,)
    # detections (CSR by facility)
    d_offset: np.ndarray             # (n_fac + 1)
    d_sensor: np.ndarray             # (n_det,)
    d_wind: np.ndarray               # (n_det,)
    d_rate: np.ndarray               # (n_det,) reported kg/h


@dataclass
class SurveyObs:
    """Per-source survey detections, grouped per (facility, visit). Non-detected candidate rows are counted."""

    v_offset: np.ndarray             # (n_fac + 1) visits CSR
    v_sensor: np.ndarray             # (n_visits,)
    v_wind: np.ndarray               # (n_visits,)
    v_n_rows: np.ndarray             # (n_visits,) candidate-source rows surveyed (usable)
    r_offset: np.ndarray             # (n_visits + 1) detections CSR within visits
    r_rate: np.ndarray               # (n_det_total,)


@dataclass
class CMSSummary:
    """Run-length summary of the usable hourly series per instrumented facility (TDD section 6.4, v1)."""

    facility: np.ndarray             # (n_cms,) facility index
    sensor: np.ndarray               # (n_cms,) sensor index
    n_usable: np.ndarray             # usable hours
    n_detected: np.ndarray           # detected usable hours
    n_runs_det: np.ndarray           # number of detected runs
    n_runs_nd: np.ndarray            # number of non-detected runs
    mean_run_det: np.ndarray         # mean detected-run length (h), nan if none
    mean_ln_rate: np.ndarray         # mean of ln reported rate over detected hours, nan if none


@dataclass
class EstimatorInputs:
    n_fac: int
    year: int
    sensor_keys: tuple[str, ...]
    stratum_idx: np.ndarray
    rho_surf: np.ndarray
    f_gas: np.ndarray
    g_hat_kg_yr: np.ndarray          # noisy denominator
    sigma_g: np.ndarray              # per facility (TDD section 6.6)
    snapshots: SnapshotGroups
    surveys: SurveyObs
    cms: CMSSummary
    meta: dict[str, Any] = field(default_factory=dict)
    # per-facility overrides of the stratum priors (equipment-based leak model; NaN = stratum prior applies)
    prior_overrides: dict[str, np.ndarray] | None = None

    def facility_has_cms(self) -> np.ndarray:
        has = np.zeros(self.n_fac, dtype=bool)
        has[self.cms.facility] = True
        return has


def _csr(fac: np.ndarray, n_fac: int) -> tuple[np.ndarray, np.ndarray]:
    """Sort key -> (order, offsets) for a facility-indexed array."""
    order = np.argsort(fac, kind="stable")
    counts = np.bincount(fac, minlength=n_fac)
    return order, np.concatenate([[0], np.cumsum(counts)])


def _snapshot_groups(log: ObservationLog, library: SensorLibrary, n_fac: int) -> SnapshotGroups:
    site = (log.source_idx < 0) & log.usable
    fac, sen, wind, det, rate = (a[site] for a in (log.facility_idx, log.sensor_idx.astype(np.int64), log.wind_m_s.astype(float), log.detected, log.reported_kg_h))
    # non-detections -> groups
    nd = ~det
    wbin = np.round(wind[nd] / WIND_BIN_M_S).astype(np.int64)
    key = (fac[nd] * 64 + sen[nd]) * 4096 + np.clip(wbin, 0, 4095)
    uniq, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    g_fac = uniq // (64 * 4096); g_sen = (uniq // 4096) % 64; g_w = (uniq % 4096) * WIND_BIN_M_S
    order, off = _csr(g_fac, n_fac)
    # detections individually
    dfac, dsen, dwind, drate = fac[det], sen[det], wind[det], rate[det]
    dorder, doff = _csr(dfac, n_fac)
    return SnapshotGroups(nd_offset=off, nd_sensor=g_sen[order], nd_wind=g_w[order].astype(float), nd_count=cnt[order].astype(np.int64),
                          d_offset=doff, d_sensor=dsen[dorder], d_wind=dwind[dorder], d_rate=drate[dorder])


def _surveys(log: ObservationLog, n_fac: int) -> SurveyObs:
    src = (log.source_idx >= 0) & log.usable
    if not src.any():
        z = np.zeros(n_fac + 1, np.int64)
        return SurveyObs(z, np.array([], np.int64), np.array([]), np.array([], np.int64), np.zeros(1, np.int64), np.array([]))
    fac, sen, hour, wind, det, rate = (a[src] for a in (log.facility_idx, log.sensor_idx.astype(np.int64), log.hour_idx, log.wind_m_s.astype(float), log.detected, log.reported_kg_h))
    vkey = (fac * 64 + sen) * 10000 + hour
    uniq, inv, nrows = np.unique(vkey, return_inverse=True, return_counts=True)
    v_fac = uniq // (64 * 10000); v_sen = (uniq // 10000) % 64
    v_wind = np.zeros(uniq.shape[0]); np.add.at(v_wind, inv, wind); v_wind /= nrows
    # detections per visit
    ndet = np.bincount(inv[det], minlength=uniq.shape[0])
    order, off = _csr(v_fac, n_fac)
    # reorder visits by facility; detections CSR within visit order
    r_off = np.concatenate([[0], np.cumsum(ndet[order])])
    # gather detection rates in visit order
    det_visit = inv[det]; det_rate = rate[det]
    pos = np.empty(uniq.shape[0], np.int64); pos[order] = np.arange(uniq.shape[0])
    dorder = np.argsort(pos[det_visit], kind="stable")
    return SurveyObs(v_offset=off, v_sensor=v_sen[order], v_wind=v_wind[order], v_n_rows=nrows[order].astype(np.int64),
                     r_offset=r_off, r_rate=det_rate[dorder])


def cms_summary_from_series(usable: np.ndarray, detected: np.ndarray, reported: np.ndarray) -> dict[str, np.ndarray]:
    """Run-length statistics of usable hours only (non-usable hours are spliced out)."""
    n_f, T = detected.shape
    n_usable = usable.sum(axis=1)
    det_u = detected & usable
    n_det = det_u.sum(axis=1)
    # runs over the usable-only sequence: mark positions where the usable flag changes state relative to the previous usable hour
    # implement per row with compressed sequences (vectorised via cumulative trick)
    runs_det = np.zeros(n_f, np.int64); runs_nd = np.zeros(n_f, np.int64); mean_run_det = np.full(n_f, np.nan)
    mean_ln_rate = np.full(n_f, np.nan)
    for i in range(n_f):                     # n_cms facilities only; each row vectorised
        seq = det_u[i, usable[i]]
        if seq.size == 0:
            continue
        change = np.diff(seq.astype(np.int8))
        starts = np.concatenate([[0], np.nonzero(change != 0)[0] + 1])
        lengths = np.diff(np.concatenate([starts, [seq.size]]))
        states = seq[starts]
        runs_det[i] = int(states.sum()); runs_nd[i] = int((~states).sum())
        if runs_det[i]:
            mean_run_det[i] = lengths[states].mean()
        if n_det[i]:
            r = reported[i][det_u[i]]
            r = r[np.isfinite(r) & (r > 0)]
            if r.size:
                mean_ln_rate[i] = np.log(r).mean()
    return {"n_usable": n_usable, "n_detected": n_det, "n_runs_det": runs_det, "n_runs_nd": runs_nd,
            "mean_run_det": mean_run_det, "mean_ln_rate": mean_ln_rate}


def _cms(cms_logs: dict[str, CMSLog], sensor_keys: tuple[str, ...]) -> CMSSummary:
    facs, sens, parts = [], [], []
    for key, c in cms_logs.items():
        stats = cms_summary_from_series(c.usable, c.detected, c.reported_kg_h)
        facs.append(c.facilities); sens.append(np.full(c.facilities.shape[0], sensor_keys.index(key)))
        parts.append(stats)
    if not parts:
        e = np.array([], np.int64)
        return CMSSummary(e, e, e, e, e, e, np.array([]), np.array([]))
    cat = lambda k: np.concatenate([p[k] for p in parts])  # noqa: E731
    return CMSSummary(np.concatenate(facs), np.concatenate(sens), cat("n_usable"), cat("n_detected"), cat("n_runs_det"),
                      cat("n_runs_nd"), cat("mean_run_det"), cat("mean_ln_rate"))


def build_inputs(pop: Population, obs: ObservationSet, library: SensorLibrary, seeds: SeedTree) -> EstimatorInputs:
    """Assemble estimator inputs; the only truth used is G_i to generate the noisy G_hat (TDD section 6.6)."""
    n = pop.n_facilities
    sigma_g = np.where(pop.throughput.ghgrp_reporter, SIGMA_G_GHGRP, SIGMA_G_OTHER)
    eta = seeds.rng("estimate", "denominator").normal(0.0, 1.0, size=n) * sigma_g
    g_hat = pop.throughput.g_ch4_kg_yr * np.exp(eta)
    return EstimatorInputs(
        n_fac=n, year=obs.year, sensor_keys=obs.log.sensor_keys, stratum_idx=pop.stratum_idx,
        rho_surf=pop.conditions.surface_reflectance.astype(float), f_gas=pop.throughput.f_gas, g_hat_kg_yr=g_hat, sigma_g=sigma_g,
        snapshots=_snapshot_groups(obs.log, library, n), surveys=_surveys(obs.log, n), cms=_cms(obs.cms, obs.log.sensor_keys),
        meta={"wind_bin_m_s": WIND_BIN_M_S}, prior_overrides=pop.prior_overrides,
    )


def evidence_counts(inputs: EstimatorInputs) -> np.ndarray:
    """(3, n_fac) int: usable site snapshots, usable survey visits, usable CMS hours per facility (PRD section 5.4a).

    Counts what the estimator actually saw; blocked opportunities (cloud, sun, wind) are not evidence.
    """
    n = inputs.n_fac
    sn, sv, cm = inputs.snapshots, inputs.surveys, inputs.cms
    snaps = np.diff(sn.d_offset).astype(np.int64)
    for f in range(n):
        snaps[f] += int(sn.nd_count[sn.nd_offset[f]:sn.nd_offset[f + 1]].sum())
    visits = np.diff(sv.v_offset).astype(np.int64)
    cms_h = np.zeros(n, dtype=np.int64)
    np.add.at(cms_h, cm.facility, cm.n_usable.astype(np.int64))
    return np.stack([snaps, visits, cms_h])
