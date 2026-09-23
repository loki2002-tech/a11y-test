"""
pytest configuration and fixtures.

Shared fixtures for all test categories.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Configure logging before test run."""
    from accessibility_agent.logging_config import configure_logging
    configure_logging()
