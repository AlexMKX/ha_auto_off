"""Editable text entity for Auto Off group delay configuration."""
import logging
from typing import Dict, TYPE_CHECKING

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_DELAY

if TYPE_CHECKING:
    from .integration_manager import IntegrationManager

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


class DelayTextEntity(TextEntity):
    """Text entity for editing delay in minutes (can be template)."""

    _attr_has_entity_name = True
    _attr_mode = TextMode.TEXT
    _attr_native_max = 255
    _attr_name = "Delay (minutes)"

    def __init__(
        self,
        hass: HomeAssistant,
        manager: "IntegrationManager",
        group_name: str,
        config_dict: Dict,
    ) -> None:
        """Initialize delay text entity."""
        self.hass = hass
        self._manager = manager
        self._group_name = group_name
        self._config_dict = config_dict
        self._attr_unique_id = f"{DOMAIN}_{group_name}_delay"
        self._update_native_value()

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

    def _update_native_value(self) -> None:
        """Update native value from config."""
        delay = self._config_dict.get(CONF_DELAY, 0)
        self._attr_native_value = str(delay)

    @callback
    def update_config(self, config_dict: Dict) -> None:
        """Update the config from external source."""
        self._config_dict = config_dict
        self._update_native_value()
        self.async_write_ha_state()

    async def async_set_value(self, value: str) -> None:
        """Handle value change from UI."""
        value = value.strip()
        
        # Try to parse as int, otherwise keep as string (template)
        try:
            delay = int(value)
        except ValueError:
            delay = value  # Keep as string for template support
        
        new_config = dict(self._config_dict)
        new_config[CONF_DELAY] = delay
        
        _LOGGER.info(f"Updating delay for group '{self._group_name}': {delay}")
        await self._manager.update_group_config(self._group_name, new_config)
