"""Seed tree tests (PRD N4)."""

from __future__ import annotations

import numpy as np
import pytest

from mrvsim.io.seeds import SeedTree, stream_key


def test_same_path_same_stream() -> None:
    t = SeedTree(42)
    a = t.rng("population", "rates", stratum=3).random(5)
    b = t.rng("population", "rates", stratum=3).random(5)
    np.testing.assert_array_equal(a, b)


def test_keyword_order_irrelevant() -> None:
    assert stream_key("a", rep=1, stratum=2) == stream_key("a", stratum=2, rep=1)


def test_different_paths_differ() -> None:
    t = SeedTree(42)
    a = t.rng("population", "rates").random(5)
    b = t.rng("population", "durations").random(5)
    c = t.rng("population", "rates", rep=1).random(5)
    assert not np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_different_master_seeds_differ() -> None:
    a = SeedTree(1).rng("x").random(5)
    b = SeedTree(2).rng("x").random(5)
    assert not np.array_equal(a, b)


def test_child_scoping_equals_prefixed_path() -> None:
    t = SeedTree(7)
    via_child = t.child(rep=3).rng("observe", "aircraft").random(4)
    via_path = t.rng("rep=3", "observe", "aircraft").random(4)
    np.testing.assert_array_equal(via_child, via_path)


def test_request_order_independent() -> None:
    t = SeedTree(99)
    a1 = t.rng("a").random(3)
    b1 = t.rng("b").random(3)
    t2 = SeedTree(99)
    b2 = t2.rng("b").random(3)
    a2 = t2.rng("a").random(3)
    np.testing.assert_array_equal(a1, a2)
    np.testing.assert_array_equal(b1, b2)


def test_raw_bitstream_is_frozen() -> None:
    """Guards against silent changes to the derivation scheme.

    PCG64's raw output is stable across numpy versions; if this fails, the
    hashing or spawn-key layout changed and every recorded run is invalidated.
    """
    t = SeedTree(20260930)
    raw = t.rng("population", "rates", stratum=0).bit_generator.random_raw(3)
    expected = np.array(_FROZEN_RAW, dtype=np.uint64)
    np.testing.assert_array_equal(raw, expected)


# Filled in once at Phase 0 from the first run; see test above.
_FROZEN_RAW = [11566712320197871390, 116562470026568421, 2573898232163690563]


def test_integer_seed_stable_and_distinct() -> None:
    t = SeedTree(5)
    s1 = t.integer_seed("pymc", facility=1)
    s2 = t.integer_seed("pymc", facility=1)
    s3 = t.integer_seed("pymc", facility=2)
    assert s1 == s2 != s3
    assert 0 <= s1 < 2**63


def test_invalid_seed_rejected() -> None:
    with pytest.raises(ValueError):
        SeedTree(-1)
    with pytest.raises(ValueError):
        SeedTree(2**63)


def test_slash_in_path_rejected() -> None:
    with pytest.raises(ValueError):
        stream_key("a/b")
