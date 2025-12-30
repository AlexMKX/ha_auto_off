"""Text entities for Auto Off group configuration."""
import logging
import yaml
from typing import Any

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_GROUPS

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


class GroupConfigTextEntity(TextEntity):
    """Text entity for editing group configuration in YAML format."""

    _attr_has_entity_name = True
    _attr_name = "Config"
    _attr_mode = TextMode.TEXT
    _attr_native_max = 10000

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        group_name: str,
        config_yaml: str,
    ) -> None:
        """Initialize the text entity."""
        self.hass = hass
        self._entry = entry
        self._group_name = group_name
        self._attr_native_value = config_yaml
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

    async def async_set_value(self, value: str) -> None:
        """Set the text value and update group configuration."""
        try:
            # Validate YAML
            config_dict = yaml.safe_load(value)
            if not isinstance(config_dict, dict):
                _LOGGER.error(f"Invalid config format for group '{self._group_name}': must be a dictionary")
                return

            # Get manager and update group
            manager = self.hass.data.get(DOMAIN)
            if manager:
                await manager.update_group_config(self._group_name, value)
                self._attr_native_value = value
                self.async_write_ha_state()
                _LOGGER.info(f"Group '{self._group_name}' configuration updated")
            else:
                _LOGGER.error("Integration manager not found")

        except yaml.YAMLError as e:
            _LOGGER.error(f"Invalid YAML in group '{self._group_name}' config: {e}")
        except Exception as e:
            _LOGGER.error(f"Failed to update group '{self._group_name}' config: {e}")

    @callback
    def update_config(self, config_yaml: str) -> None:
        """Update the config value externally."""
        self._attr_native_value = config_yaml
        self.async_write_ha_state()
