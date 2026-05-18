"""Shared pytest fixtures for the SDK test suite."""

from __future__ import annotations

import pytest

from muninn import MuninnClient


@pytest.fixture
def client() -> MuninnClient:
    """A MuninnClient pointing at an in-memory respx mock."""
    return MuninnClient(host="http://muninn.test", timeout=5.0)
