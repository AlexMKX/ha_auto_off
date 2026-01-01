"""Pytest fixtures for auto_off tests."""
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry


# Configure pytest-asyncio
pytest_plugins = ('pytest_asyncio',)


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock(spec=HomeAssistant)
    hass.data = {}
    hass.loop = MagicMock()
    hass.loop.time = MagicMock(return_value=1000.0)
    hass.states = MagicMock()
    hass.services = MagicMock()
    hass.config_entries = MagicMock()
    hass.async_create_task = MagicMock()
    return hass


@pytest.fixture
def config_entry():
    """Create a mock config entry."""
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "test_entry_id"
    entry.data = {
        "poll_interval": 15,
        "groups": {},
    }
    entry.options = {}
    return entry


@pytest.fixture
def async_add_entities():
    """Create a mock async_add_entities callback."""
    return MagicMock()


@pytest.fixture
def sample_group_config_yaml():
    """Sample group configuration in YAML format."""
    return """sensors:
  - binary_sensor.motion_living_room
targets:
  - light.living_room
delay: 5
"""


@pytest.fixture
def sample_group_config_dict():
    """Sample group configuration as dict."""
    return {
        "sensors": ["binary_sensor.motion_living_room"],
        "targets": ["light.living_room"],
        "delay": 5,
    }
