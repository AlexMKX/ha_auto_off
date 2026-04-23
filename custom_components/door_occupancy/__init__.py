"""Door Occupancy integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_OCCUPANCY_TIMEOUT,
    DEFAULT_OCCUPANCY_TIMEOUT,
    DOMAIN,
    PLATFORMS,
)
from .discovery import DoorOccupancyManager

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Door Occupancy config entry."""
    hass.data.setdefault(DOMAIN, {})
    manager = DoorOccupancyManager(
        hass,
        entry,
        occupancy_timeout=entry.data.get(
            CONF_OCCUPANCY_TIMEOUT, DEFAULT_OCCUPANCY_TIMEOUT
        ),
    )
    hass.data[DOMAIN][entry.entry_id] = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Door Occupancy config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        manager: DoorOccupancyManager | None = hass.data.get(DOMAIN, {}).pop(
            entry.entry_id, None
        )
        if manager is not None:
            await manager.async_unload()
    return unload_ok
