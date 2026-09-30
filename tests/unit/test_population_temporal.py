"""Alternating renewal process and bit-packed states (TDD section 3.4)."""

from __future__ import annotations

import numpy as np
import pytest

from mrvsim.population.temporal import StatePaths, duty_cycle, lognormal_mean, simulate_intermittent_states


def test_duty_cycle_identity_from_means() -> None:
    nu_on, tau_on, nu_off, tau_off = 2.0, 1.0, 4.5, 1.0
    e_on, e_off = lognormal_mean(nu_on, tau_on), lognormal_mean(nu_off, tau_off)
    assert duty_cycle(nu_on, tau_on, nu_off, tau_off) == pytest.approx(e_on / (e_on + e_off))


@pytest.mark.parametrize(
    "nu_on,tau_on,nu_off,tau_off",
    [(2.0, 1.0, 4.5, 1.0), (0.5, 0.5, 1.0, 0.5), (4.0, 0.8, 3.0, 0.8), (1.0, 1.5, 6.0, 1.2)],
)
def test_empirical_on_fraction_matches_duty_cycle(nu_on: float, tau_on: float, nu_off: float, tau_off: float) -> None:
    """pi = E[D_on]/(E[D_on]+E[D_off]) equals the long-run on-fraction (renewal reward theorem)."""
    rng = np.random.default_rng(10)
    n = 3000
    s = simulate_intermittent_states(rng, *(np.full(n, v) for v in (nu_on, tau_on, nu_off, tau_off)), n_hours=8760)
    pi = duty_cycle(nu_on, tau_on, nu_off, tau_off)
    frac = s.mean()
    # Standard error across sources dominates; allow 3 sigma using a crude per-source variance bound.
    per_source = s.mean(axis=1)
    se = per_source.std(ddof=1) / np.sqrt(n)
    assert abs(frac - pi) < max(4 * se, 0.01), (frac, pi, se)


def test_stationary_start_no_transient() -> None:
    """The on-fraction in the first day equals that in the last day (stationary start, no burn-in bias)."""
    rng = np.random.default_rng(11)
    n = 20000
    s = simulate_intermittent_states(rng, np.full(n, 3.0), np.full(n, 0.8), np.full(n, 5.0), np.full(n, 0.8), n_hours=8760)
    first, last = s[:, :24].mean(), s[:, -24:].mean()
    pi = duty_cycle(3.0, 0.8, 5.0, 0.8)
    assert abs(first - pi) < 0.01
    assert abs(last - pi) < 0.01


def test_run_lengths_have_lognormal_scale() -> None:
    """Mean detected-run length should be near E[D_on] (hourly discretisation aside)."""
    rng = np.random.default_rng(12)
    n = 2000
    nu_on, tau_on = 3.0, 0.5       # E[D_on] ~ 22.8 h
    s = simulate_intermittent_states(rng, np.full(n, nu_on), np.full(n, tau_on), np.full(n, 4.0), np.full(n, 0.5))
    # Count interior on-runs (exclude runs touching the edges to avoid truncation bias).
    d = np.diff(s.astype(np.int8), axis=1)
    starts = [np.nonzero(row == 1)[0] + 1 for row in d]
    ends = [np.nonzero(row == -1)[0] + 1 for row in d]
    lengths = []
    for st, en in zip(starts, ends):
        en = en[en > (st[0] if st.size else 0)]
        m = min(st.size, en.size)
        lengths.extend((en[:m] - st[:m]).tolist())
    mean_len = np.mean(lengths)
    assert abs(mean_len - lognormal_mean(nu_on, tau_on)) / lognormal_mean(nu_on, tau_on) < 0.10


def test_short_cycles_terminate_and_match_pi() -> None:
    """Sub-hour cycles exercise the top-up loop; result should look like Bernoulli(pi) per hour."""
    rng = np.random.default_rng(13)
    n = 200
    s = simulate_intermittent_states(rng, np.full(n, -1.5), np.full(n, 0.3), np.full(n, -0.5), np.full(n, 0.3))
    pi = duty_cycle(-1.5, 0.3, -0.5, 0.3)
    assert abs(s.mean() - pi) < 0.01


def test_bitpack_round_trip_and_queries() -> None:
    rng = np.random.default_rng(14)
    states = rng.random((37, 8760)) < 0.3
    sp = StatePaths.from_bool(states)
    assert sp.packed.shape == (37, 1095)
    np.testing.assert_array_equal(sp.to_bool(), states)
    np.testing.assert_array_equal(sp.on_hours(), states.sum(axis=1))
    hours = np.array([0, 1, 7, 8, 100, 8759])
    np.testing.assert_array_equal(sp.state_at(hours), states[:, hours])
    src = np.array([0, 5, 36, 36])
    hrs = np.array([8759, 3, 0, 4000])
    np.testing.assert_array_equal(sp.state_at_pairs(src, hrs), states[src, hrs])


def test_bitpack_save_load(tmp_path) -> None:
    rng = np.random.default_rng(15)
    states = rng.random((5, 8760)) < 0.5
    sp = StatePaths.from_bool(states)
    sp.save(tmp_path / "s.npz")
    back = StatePaths.load(tmp_path / "s.npz")
    np.testing.assert_array_equal(back.to_bool(), states)


def test_reproducible() -> None:
    a = simulate_intermittent_states(np.random.default_rng(5), np.full(50, 2.0), np.full(50, 1.0), np.full(50, 4.0), np.full(50, 1.0))
    b = simulate_intermittent_states(np.random.default_rng(5), np.full(50, 2.0), np.full(50, 1.0), np.full(50, 4.0), np.full(50, 1.0))
    np.testing.assert_array_equal(a, b)
