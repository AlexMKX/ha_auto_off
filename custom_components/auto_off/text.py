"""Text entities for Auto Off group configuration."""
import logging
from typing import Any, Dict

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_GROUPS, CONF_SENSORS, CONF_TARGETS, CONF_DELAY

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up text entities from a config entry."""
    manager = hass.data.get(DOMAIN)
    if manager:
        manager.text_platform_ready(async_add_entities)


def _config_to_display(config_dict: Dict) -> str:
    """Convert config dict to display string."""
    sensors = config_dict.get(CONF_SENSORS, [])
    targets = config_dict.get(CONF_TARGETS, [])
    delay = config_dict.get(CONF_DELAY, 0)
    return f"sensors: {len(sensors)}, targets: {len(targets)}, delay: {delay}"


class GroupConfigTextEntity(TextEntity):
    """Text entity for displaying group configuration summary."""

    _attr_has_entity_name = True
    _attr_name = "Config"
    _attr_mode = TextMode.TEXT
    _attr_native_max = 10000

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        group_name: str,
        config_dict: Dict,
    ) -> None:
        """Initialize the text entity."""
        self.hass = hass
        self._entry = entry
        self._group_name = group_name
        self._config_dict = config_dict
        self._attr_native_value = _config_to_display(config_dict)
        self._attr_unique_id = f"{DOMAIN}_{group_name}_config"

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
        return {
            CONF_SENSORS: self._config_dict.get(CONF_SENSORS, []),
            CONF_TARGETS: self._config_dict.get(CONF_TARGETS, []),
            CONF_DELAY: self._config_dict.get(CONF_DELAY, 0),
        }

    async def async_set_value(self, value: str) -> None:
        """Set value is not supported - use set_group service instead."""
        _LOGGER.warning(
            f"Direct text edit not supported for group '{self._group_name}'. "
            "Use auto_off.set_group service instead."
        )

    @callback
    def update_config(self, config_dict: Dict) -> None:
        """Update the config value externally."""
        self._config_dict = config_dict
        self._attr_native_value = _config_to_display(config_dict)
        self.async_write_ha_state()
