"""Shared test fixtures for ACF test suite."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the global rate limiter before each test to prevent 429s in the suite."""
    from api.main import rate_limiter
    rate_limiter.reset()
