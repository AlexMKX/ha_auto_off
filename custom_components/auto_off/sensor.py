"""Sensor entities for Auto Off group configuration."""
import logging
from typing import Any, Dict

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_SENSORS, CONF_TARGETS, CONF_DELAY

_LOGGER = logging.getLogger(__name__)


def _format_delay(seconds: int) -> str:
    """Format delay in human-readable form."""
    if seconds >= 60:
        minutes = seconds // 60
        remaining_seconds = seconds % 60
        if remaining_seconds:
            return f"{minutes}m {remaining_seconds}s"
        return f"{minutes}m"
    return f"{seconds}s"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry."""
    manager = hass.data.get(DOMAIN)
    if manager:
        manager.sensor_platform_ready(async_add_entities)


class GroupConfigSensorEntity(SensorEntity):
    """Sensor entity for displaying group configuration summary."""

    _attr_has_entity_name = True
    _attr_name = "Config"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        group_name: str,
        config_dict: Dict,
    ) -> None:
        """Initialize the sensor entity."""
        self.hass = hass
        self._entry = entry
        self._group_name = group_name
        self._config_dict = config_dict
        self._attr_unique_id = f"{DOMAIN}_{group_name}_config"
        self._update_state()

    def _update_state(self) -> None:
        """Update native value from config."""
        sensors = self._config_dict.get(CONF_SENSORS, [])
        targets = self._config_dict.get(CONF_TARGETS, [])
        delay = self._config_dict.get(CONF_DELAY, 0)
        self._attr_native_value = f"{len(sensors)} sensors → {len(targets)} targets ({_format_delay(delay)})"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._group_name)},
            name=f"Auto Off: {self._group_name}",
            manufacturer="Auto Off",
            model="Sensor Group",
            sw_version="1.0",
        )

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes with full config."""
        sensors = self._config_dict.get(CONF_SENSORS, [])
        targets = self._config_dict.get(CONF_TARGETS, [])
        delay = self._config_dict.get(CONF_DELAY, 0)
        
        return {
            "delay": _format_delay(delay),
            "sensors": sensors,
            "targets": targets,
        }

    @callback
    def update_config(self, config_dict: Dict) -> None:
        """Update the config value externally."""
        self._config_dict = config_dict
        self._update_state()
        self.async_write_ha_state()


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
    ) -> None:
        """Initialize the deadline sensor entity."""
        self.hass = hass
        self._entry = entry
        self._group_name = group_name
        self._attr_unique_id = f"{DOMAIN}_{group_name}_deadline"
        self._attr_native_value = None
        self._deadline_iso: str | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this entity."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._group_name)},
            name=f"Auto Off: {self._group_name}",
            manufacturer="Auto Off",
            model="Sensor Group",
            sw_version="1.0",
        )

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return extra state attributes."""
        return {
            "deadline_iso": self._deadline_iso,
        }

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
