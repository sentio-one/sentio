"""Fixtures for native WhatsApp business configuration tests."""

import pytest


@pytest.fixture
def business_config():
    """Return the minimal disabled business configuration."""
    return {}
