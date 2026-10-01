"""Validation tests V1-V7 (TDD section 9).

Each test calls the corresponding ``mrvsim.validate`` module and asserts a pass. When the priors are PLACEHOLDER
the module returns ``skipped`` with a refusal reason and the test is skipped (CLAUDE.md Phase 6: never mark a
validation test as passing against placeholder priors). Set MRVSIM_ALLOW_PLACEHOLDER_VALIDATION=1 to run them as
diagnostics; the results are then flagged ``diagnostic_only`` and the tests still assert the pass rule.
"""

from __future__ import annotations

import pytest

from mrvsim.validate.results import ValidationResult


def assert_validation(res: ValidationResult) -> None:
    if res.status == "skipped":
        pytest.skip(f"{res.id} skipped: {res.reason}")
    if res.status == "error":
        pytest.fail(f"{res.id} error: {res.reason}")
    assert res.status == "pass", f"{res.id} failed: {res.compared}"
