"""Validation tests V1-V7 (TDD section 9) as importable modules; pytest wrappers live in tests/validation."""

from mrvsim.validate.results import ValidationResult, placeholder_allowed, write_results
from mrvsim.validate.targets import load_targets

__all__ = ["ValidationResult", "load_targets", "placeholder_allowed", "write_results"]
