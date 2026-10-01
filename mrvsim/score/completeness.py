"""P_bar_j = 1 - prod_k (1 - P_s(q_j, c_k)) over the year's usable opportunities (TDD section 7, Completeness).

Site-level snapshots: every usable snapshot at the facility is an opportunity for each of its sources with
POD evaluated at the source's own rate (a source is "detectable" if that sensor would see it alone).
CMS: every usable hour; the product over 8,760 hours is formed in log space. Surveys: each usable visit row.
The source's on/off state does not enter (completeness asks whether the *system* could detect the source,
not whether it happened to be on) [jacob2022].
"""

from __future__ import annotations

import numpy as np

from mrvsim.estimate.likelihood import SensorParams
from mrvsim.observe.simulator import ObservationSet
from mrvsim.population.generate import Population
from mrvsim.sensors.library import SensorLibrary


def detection_probability_once(pop: Population, obs: ObservationSet, library: SensorLibrary) -> np.ndarray:
    """Per-source probability of at least one detection over the year, given the realised usable opportunities."""
    n_src = pop.n_total_sources
    log_miss = np.zeros(n_src)
    sps = {k: SensorParams.from_sensor(library[k]) for k in obs.log.sensor_keys}
    log = obs.log
    usable = log.usable
    # site-level snapshots: expand (facility, opportunity) -> sources
    site = usable & (log.source_idx < 0)
    if site.any():
        fac = log.facility_idx[site]; sen = log.sensor_idx[site]; wind = log.wind_m_s[site].astype(float); rho = log.rho_surf[site].astype(float)
        counts = pop.n_sources[fac]
        rep = np.repeat(np.arange(fac.shape[0]), counts)
        src = np.concatenate([np.arange(pop.source_offset[f], pop.source_offset[f] + pop.n_sources[f]) for f in fac]) if fac.size else np.array([], np.int64)
        q = pop.q_kg_h[src]
        for si, key in enumerate(obs.log.sensor_keys):
            m = sen[rep] == si
            if not m.any():
                continue
            sp = sps[key]
            f = sp.q_factor(wind[rep][m], rho[rep][m])
            p = sp.pod(q[m] * f)
            np.add.at(log_miss, src[m], np.log1p(-np.clip(p, 0, 1 - 1e-12)))
    # per-source survey rows
    srow = usable & (log.source_idx >= 0)
    if srow.any():
        src = log.source_idx[srow]; sen = log.sensor_idx[srow]; wind = log.wind_m_s[srow].astype(float); rho = log.rho_surf[srow].astype(float)
        for si, key in enumerate(obs.log.sensor_keys):
            m = sen == si
            if not m.any():
                continue
            sp = sps[key]
            p = sp.pod(pop.q_kg_h[src[m]] * sp.q_factor(wind[m], rho[m]))
            np.add.at(log_miss, src[m], np.log1p(-np.clip(p, 0, 1 - 1e-12)))
    # CMS: usable hours x per-source POD (no wind term)
    for key, c in obs.cms.items():
        sp = sps[key]
        n_usable = c.usable.sum(axis=1)
        for fi, f in enumerate(c.facilities):
            s0, s1 = pop.source_offset[f], pop.source_offset[f + 1]
            p = sp.pod(pop.q_kg_h[s0:s1])
            log_miss[s0:s1] += n_usable[fi] * np.log1p(-np.clip(p, 0, 1 - 1e-12))
    return 1.0 - np.exp(log_miss)
