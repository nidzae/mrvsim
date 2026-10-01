"""V4 (TDD section 9)."""

import pytest

from mrvsim.validate import v4_stability
from tests.validation.conftest import assert_validation


@pytest.mark.validation
@pytest.mark.slow
def test_v4() -> None:
    assert_validation(v4_stability.run())
