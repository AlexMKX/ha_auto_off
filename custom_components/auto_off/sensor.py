"""Sensor entities for Auto Off group configuration."""

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, VERSION

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry."""
    manager = hass.data.get(DOMAIN)
    if manager:
        manager.sensor_platform_ready(async_add_entities)


class DeadlineSensorEntity(SensorEntity):
    """Sensor entity for displaying current deadline."""

    _attr_has_entity_name = True
    _attr_name = "Deadline"
    _attr_icon = "mdi:timer-outline"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        group_name: str,
        manager: Any,
    ) -> None:
        """Initialize the deadline sensor entity."""
        self.hass = hass
        self._entry = entry
        self._group_name = group_name
        self._attr_unique_id = f"{DOMAIN}_{group_name}_deadline"
        self._attr_native_value = "—"
        self._deadline_iso: str | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._group_name)},
            name=f"Auto Off: {self._group_name}",
            manufacturer="Auto Off",
            model="Sensor Group",
            sw_version=VERSION,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {"deadline_iso": self._deadline_iso}

    @callback
    def update_deadline(self, deadline_str: str | None) -> None:
        """Update deadline from external source."""
        self._deadline_iso = deadline_str
        if deadline_str:
            try:
                from datetime import datetime

                deadline = datetime.fromisoformat(deadline_str)
                # Format as human-readable
                self._attr_native_value = deadline.strftime("%H:%M:%S")
            except (ValueError, TypeError):
                self._attr_native_value = deadline_str
        else:
            self._attr_native_value = "—"
        self.async_write_ha_state()
