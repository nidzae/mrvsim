"""Alternating renewal process and bit-packed hourly state storage (TDD section 3.4).

Each intermittent source alternates on-durations and off-durations drawn from
independent lognormals. The hourly state path S(t), t = 0..T-1, is the state
at the midpoint of hour t. Steady sources (z = 0) are always on.

Stationary start
----------------
A renewal process observed at an arbitrary time is in the "on" state with
probability pi = E[D_on] / (E[D_on] + E[D_off]) and the residual duration of
the current interval follows the *length-biased* distribution scaled by an
independent U(0, 1). For a lognormal(nu, tau) duration the length-biased law
is lognormal(nu + tau^2, tau) [renewal-theory], so the exact stationary start
is closed-form and needs no burn-in. This is a modelling choice made during
implementation and is recorded in DECISION_LOG (2026-09-30, Phase 1).

Vectorisation
-------------
Durations for many sources are drawn as a (rows, cycles) matrix, cumulated into
change times, and converted to hourly parity via a bincount of flip indices.
Rows whose change times do not yet reach T are topped up in a loop that only
touches the unfinished rows. Rows are processed in chunks so that peak memory
stays bounded (~100 MB) regardless of population size.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

HOURS_PER_YEAR = 8760


def lognormal_mean(nu: np.ndarray | float, tau: np.ndarray | float) -> np.ndarray:
    return np.exp(np.asarray(nu, dtype=float) + 0.5 * np.asarray(tau, dtype=float) ** 2)


def duty_cycle(nu_on: np.ndarray, tau_on: np.ndarray, nu_off: np.ndarray, tau_off: np.ndarray) -> np.ndarray:
    """pi = E[D_on] / (E[D_on] + E[D_off]) (TDD section 3.4)."""
    e_on = lognormal_mean(nu_on, tau_on)
    e_off = lognormal_mean(nu_off, tau_off)
    return e_on / (e_on + e_off)


def _flip_hours_from_durations(
    rng: np.random.Generator,
    start_on: np.ndarray,
    nu_on: np.ndarray, tau_on: np.ndarray, nu_off: np.ndarray, tau_off: np.ndarray,
    n_hours: int,
    max_cols: int = 2048,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (row_index, flip_hour) pairs for a chunk of intermittent sources.

    A state change at time c (hours from the start of the year) first affects
    the hour whose midpoint is >= c, i.e. hour index ceil(c - 0.5). Changes at
    or beyond n_hours are dropped.
    """
    n = start_on.shape[0]
    # Residual of the current interval: U * length-biased duration.
    nu_first = np.where(start_on, nu_on, nu_off)
    tau_first = np.where(start_on, tau_on, tau_off)
    residual = rng.random(n) * np.exp(rng.normal(nu_first + tau_first**2, tau_first))

    rows_all: list[np.ndarray] = []
    flips_all: list[np.ndarray] = []
    active = np.arange(n)
    t_now = residual.copy()               # time of the next change, per active row
    next_is_on = ~start_on                # state that begins at the next change
    # Record the first change.
    first_h = np.ceil(t_now - 0.5).astype(np.int64)
    keep = first_h < n_hours
    rows_all.append(active[keep]); flips_all.append(first_h[keep])

    mean_cycle = lognormal_mean(nu_on, tau_on) + lognormal_mean(nu_off, tau_off)
    while active.size:
        remaining = n_hours - t_now  # t_now is already aligned with `active`
        need = np.ceil(1.3 * remaining / mean_cycle[active]).astype(np.int64) * 2 + 4
        cols = int(min(max(need.max(), 2), max_cols))
        cols += cols % 2  # even so on/off alternate cleanly
        # Durations alternate starting with the state that begins at t_now.
        on_mask = np.empty((active.size, cols), dtype=bool)
        on_mask[:, 0::2] = next_is_on[:, None]
        on_mask[:, 1::2] = ~next_is_on[:, None]
        nu = np.where(on_mask, nu_on[active, None], nu_off[active, None])
        tau = np.where(on_mask, tau_on[active, None], tau_off[active, None])
        d = np.exp(rng.normal(nu, tau))
        change = t_now[:, None] + np.cumsum(d, axis=1)
        h = np.ceil(change - 0.5).astype(np.int64)
        valid = h < n_hours
        r_idx, c_idx = np.nonzero(valid)
        rows_all.append(active[r_idx]); flips_all.append(h[r_idx, c_idx])
        # Rows whose last change is still inside the year need more cycles.
        unfinished = valid[:, -1]
        if not np.any(unfinished):
            break
        active = active[unfinished]
        t_now = change[unfinished, -1]
        next_is_on = next_is_on[unfinished]  # cols is even, so the phase is unchanged
    return np.concatenate(rows_all), np.concatenate(flips_all)


def simulate_intermittent_states(
    rng: np.random.Generator,
    nu_on: np.ndarray, tau_on: np.ndarray, nu_off: np.ndarray, tau_off: np.ndarray,
    n_hours: int = HOURS_PER_YEAR,
    chunk_rows: int = 2048,
) -> np.ndarray:
    """Hourly on/off states for intermittent sources, shape (n, n_hours), dtype bool.

    Starts each source in its stationary distribution (see module docstring).
    """
    nu_on, tau_on, nu_off, tau_off = (np.asarray(a, dtype=float) for a in (nu_on, tau_on, nu_off, tau_off))
    n = nu_on.shape[0]
    out = np.empty((n, n_hours), dtype=bool)
    if n == 0:
        return out
    pi = duty_cycle(nu_on, tau_on, nu_off, tau_off)
    start_on_all = rng.random(n) < pi
    for lo in range(0, n, chunk_rows):
        hi = min(lo + chunk_rows, n)
        sl = slice(lo, hi)
        rows, flips = _flip_hours_from_durations(
            rng, start_on_all[sl], nu_on[sl], tau_on[sl], nu_off[sl], tau_off[sl], n_hours
        )
        m = hi - lo
        counts = np.bincount(rows * n_hours + flips, minlength=m * n_hours).reshape(m, n_hours)
        parity = np.cumsum(counts.astype(np.uint8), axis=1, dtype=np.uint8) & 1  # mod-256 cumsum keeps parity
        out[sl] = start_on_all[sl, None] ^ parity.astype(bool)
    return out


@dataclass
class StatePaths:
    """Bit-packed hourly states for all sources: shape (n_sources, ceil(n_hours/8)) uint8."""

    packed: np.ndarray
    n_hours: int

    @classmethod
    def from_bool(cls, states: np.ndarray) -> "StatePaths":
        states = np.asarray(states, dtype=bool)
        return cls(packed=np.packbits(states, axis=1), n_hours=states.shape[1])

    def to_bool(self) -> np.ndarray:
        return np.unpackbits(self.packed, axis=1, count=self.n_hours).astype(bool)

    @property
    def n_sources(self) -> int:
        return int(self.packed.shape[0])

    def on_hours(self) -> np.ndarray:
        """Number of on-hours per source (population count of the packed bits)."""
        return np.unpackbits(self.packed, axis=1, count=self.n_hours).sum(axis=1).astype(np.int64)

    def state_at(self, hours: np.ndarray) -> np.ndarray:
        """States at the given hour indices: shape (n_sources, len(hours)) bool."""
        hours = np.asarray(hours, dtype=np.int64)
        byte = hours // 8
        bit = 7 - (hours % 8)
        return ((self.packed[:, byte] >> bit) & 1).astype(bool)

    def state_at_pairs(self, source_idx: np.ndarray, hours: np.ndarray) -> np.ndarray:
        """States for paired (source, hour) indices: shape (len,) bool."""
        source_idx = np.asarray(source_idx, dtype=np.int64)
        hours = np.asarray(hours, dtype=np.int64)
        return ((self.packed[source_idx, hours // 8] >> (7 - (hours % 8))) & 1).astype(bool)

    def nbytes(self) -> int:
        return int(self.packed.nbytes)

    def save(self, path: Path) -> None:
        np.savez_compressed(path, packed=self.packed, n_hours=np.int64(self.n_hours))

    @classmethod
    def load(cls, path: Path) -> "StatePaths":
        with np.load(path) as z:
            return cls(packed=z["packed"], n_hours=int(z["n_hours"]))
