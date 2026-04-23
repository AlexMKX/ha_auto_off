"""Pytest fixtures for door_occupancy tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant


@pytest.fixture
def hass():
    """Minimal HA mock sufficient for unit-level tests."""
    hass = MagicMock(spec=HomeAssistant)
    hass.data = {}
    hass.states = MagicMock()
    hass.services = MagicMock()
    hass.config_entries = MagicMock()
    hass.async_create_task = MagicMock()
    return hass
