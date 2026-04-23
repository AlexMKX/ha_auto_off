"""Legacy binary_sensor platform for auto_off.

The auto_off integration no longer exposes any binary_sensor entities of
its own. This file is kept until PLATFORMS is reduced in the next task
so that async_forward_entry_setups does not complain about a missing
platform module.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .integration_manager import async_setup_integration


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Compatibility shim; does not register any entities."""
    await async_setup_integration(hass, entry, async_add_entities)
