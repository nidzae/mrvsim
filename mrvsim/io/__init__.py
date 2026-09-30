"""Configuration loading, seed derivation, and run persistence (CLAUDE.md: mrvsim/io).

Guarantees PRD N4: every random draw in a run derives from the run's master
seed through a named stream tree, and every run directory records the config,
seed, git SHA, and elapsed time.
"""

from mrvsim.io.config import RunConfig, canonical_yaml, config_hash, load_config
from mrvsim.io.run import RunContext, RunManifest, git_sha
from mrvsim.io.seeds import SeedTree, stream_key

__all__ = [
    "RunConfig",
    "RunContext",
    "RunManifest",
    "SeedTree",
    "canonical_yaml",
    "config_hash",
    "git_sha",
    "load_config",
    "stream_key",
]
