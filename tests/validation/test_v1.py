"""V1 (TDD section 9)."""

import pytest

from mrvsim.validate import v1_rates
from tests.validation.conftest import assert_validation


@pytest.mark.validation
@pytest.mark.slow
def test_v1() -> None:
    assert_validation(v1_rates.run())
