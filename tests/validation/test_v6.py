"""V6 (TDD section 9)."""

import pytest

from mrvsim.validate import v6_transient
from tests.validation.conftest import assert_validation


@pytest.mark.validation
@pytest.mark.slow
def test_v6() -> None:
    assert_validation(v6_transient.run())
