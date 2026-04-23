"""valve platform for Auto Off — hosts ValveGroup target entities."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_PLATFORM = "valve"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register the valve platform with the auto_off manager."""
    manager = hass.data.get(DOMAIN)
    if manager is None:
        return
    manager.register_platform_callback(_PLATFORM, async_add_entities)
