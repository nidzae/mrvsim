"""V2 (TDD section 9)."""

import pytest

from mrvsim.validate import v2_totals
from tests.validation.conftest import assert_validation


@pytest.mark.validation
@pytest.mark.slow
def test_v2() -> None:
    assert_validation(v2_totals.run())
