"""Deterministic seed derivation (PRD N4; CLAUDE.md Phase 0).

Every random draw in MRVSim comes from a :class:`numpy.random.Generator`
obtained from a :class:`SeedTree`. A tree is rooted at one integer master seed
and hands out independent, reproducible streams addressed by a *path* of
strings and integers, e.g. ``tree.rng("population", "rates", stratum=3)`` or
``tree.rng("observe", "aircraft", rep=17)``.

Design
------
* The master seed feeds :class:`numpy.random.SeedSequence`.
* A path is hashed with BLAKE2b into four 32-bit words that become the
  ``spawn_key`` of a child ``SeedSequence``. Two different paths therefore get
  streams that are independent to the quality of SeedSequence's mixing, and the
  same path always gets the same stream, regardless of the order in which
  streams were requested. Order independence matters: vectorised code may
  request streams in a different order than a debug loop, and both must agree.
* Streams use PCG64 [numpy-random]. Its raw bit stream is stable across numpy
  versions, which the unit tests rely on.

References: [numpy-random] (see ``docs/REFERENCES.md``).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Mapping

import numpy as np

PathPart = str | int

_MAX_SEED = 2**63 - 1


def stream_key(*parts: PathPart, **named: PathPart) -> tuple[int, int, int, int]:
    """Hash a stream path into a 4-word ``spawn_key`` for ``SeedSequence``.

    Positional parts are joined in order; keyword parts are appended sorted by
    name so that ``stream_key("a", rep=1, stratum=2)`` and
    ``stream_key("a", stratum=2, rep=1)`` are identical.

    >>> stream_key("population", "rates", stratum=3) == stream_key("population", "rates", stratum=3)
    True
    """
    tokens = [f"{p!s}" if isinstance(p, str) else f"#{int(p)}" for p in parts]
    tokens.extend(f"{k}={named[k]!s}" for k in sorted(named))
    if not tokens:
        raise ValueError("a stream path needs at least one part")
    for tok in tokens:
        if "/" in tok:
            raise ValueError(f"stream path parts may not contain '/': {tok!r}")
    digest = hashlib.blake2b("/".join(tokens).encode("utf-8"), digest_size=16).digest()
    words = np.frombuffer(digest, dtype="<u4")
    return tuple(int(w) for w in words)  # type: ignore[return-value]


@dataclass(frozen=True)
class SeedTree:
    """A tree of named random streams rooted at one master seed.

    Parameters
    ----------
    master_seed:
        Non-negative integer below 2**63. Recorded in every run manifest.
    prefix:
        Path prefix applied to every stream requested from this tree. Used by
        :meth:`child` to scope a subsystem (e.g. one Monte Carlo replication).
    """

    master_seed: int
    prefix: tuple[PathPart, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        seed = int(self.master_seed)
        if seed < 0 or seed > _MAX_SEED:
            raise ValueError(f"master_seed must be in [0, 2**63-1], got {self.master_seed}")
        object.__setattr__(self, "master_seed", seed)
        object.__setattr__(self, "prefix", tuple(self.prefix))

    def child(self, *parts: PathPart, **named: PathPart) -> "SeedTree":
        """Return a tree whose streams are all scoped under ``parts``.

        ``tree.child(rep=3).rng("observe")`` equals ``tree.rng(rep=3, "observe")``
        would if Python allowed that ordering; concretely it equals
        ``tree.rng("rep=3", "observe")`` after keyword normalisation.
        """
        extra = list(parts) + [f"{k}={named[k]!s}" for k in sorted(named)]
        return SeedTree(self.master_seed, self.prefix + tuple(extra))

    def seed_sequence(self, *parts: PathPart, **named: PathPart) -> np.random.SeedSequence:
        """The ``SeedSequence`` for a stream path (rarely needed directly)."""
        key = stream_key(*(self.prefix + tuple(parts)), **named)
        return np.random.SeedSequence(entropy=self.master_seed, spawn_key=key)

    def rng(self, *parts: PathPart, **named: PathPart) -> np.random.Generator:
        """A fresh PCG64 ``Generator`` for the stream path.

        Calling twice with the same path returns two generators in the same
        state; callers that need a continuing stream must keep the object.
        """
        return np.random.Generator(np.random.PCG64(self.seed_sequence(*parts, **named)))

    def integer_seed(self, *parts: PathPart, **named: PathPart) -> int:
        """A 63-bit integer seed for libraries that take one (PyMC, Optuna).

        Derived from the same stream tree, so it is as reproducible as any
        ``rng`` stream.
        """
        state = self.seed_sequence(*parts, **named).generate_state(2, dtype=np.uint32)
        return int((int(state[0]) << 31) ^ int(state[1]))

    def rngs(self, base: Iterable[PathPart], indices: Iterable[int], name: str = "i") -> list[np.random.Generator]:
        """Convenience: one generator per index under a common base path."""
        base_t = tuple(base)
        return [self.rng(*base_t, **{name: i}) for i in indices]

    def describe(self) -> Mapping[str, object]:
        return {"master_seed": self.master_seed, "prefix": list(self.prefix)}
