"""binary_sensor platform for Auto Off — hosts SensorsGroup entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_PLATFORM = "binary_sensor"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register the binary_sensor platform with the auto_off manager."""
    manager = hass.data.get(DOMAIN)
    if manager is None:
        return
    manager.register_platform_callback(_PLATFORM, async_add_entities)
