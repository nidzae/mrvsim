"""V3 (TDD section 9)."""

import pytest

from mrvsim.validate import v3_intensity
from tests.validation.conftest import assert_validation


@pytest.mark.validation
@pytest.mark.slow
def test_v3() -> None:
    assert_validation(v3_intensity.run())
