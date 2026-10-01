"""V5 (TDD section 9)."""

import pytest

from mrvsim.validate import v5_misspecification
from tests.validation.conftest import assert_validation


@pytest.mark.validation
@pytest.mark.slow
def test_v5() -> None:
    assert_validation(v5_misspecification.run())
