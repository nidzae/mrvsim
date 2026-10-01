"""V7 (TDD section 9)."""

import pytest

from mrvsim.validate import v7_calibration
from tests.validation.conftest import assert_validation


@pytest.mark.validation
@pytest.mark.slow
def test_v7() -> None:
    assert_validation(v7_calibration.run())
