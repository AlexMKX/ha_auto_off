"""Editable text entities for Auto Off group configuration."""
import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_SENSORS, CONF_TARGETS, CONF_DELAY

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


class BaseGroupTextEntity(TextEntity):
    """Base text entity for group configuration fields."""

    _attr_has_entity_name = True
    _attr_mode = TextMode.TEXT
    _attr_native_max = 10000

    def __init__(
        self,
        hass: HomeAssistant,
        manager: "IntegrationManager",
        group_name: str,
        config_dict: Dict,
    ) -> None:
        """Initialize the text entity."""
        self.hass = hass
        self._manager = manager
        self._group_name = group_name
        self._config_dict = config_dict

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

    def _get_updated_config(self) -> Dict:
        """Get updated config dict - override in subclass."""
        return dict(self._config_dict)

    @callback
    def update_config(self, config_dict: Dict) -> None:
        """Update the config from external source."""
        self._config_dict = config_dict
        self._update_native_value()
        self.async_write_ha_state()

    def _update_native_value(self) -> None:
        """Update native value from config - override in subclass."""
        pass


class SensorsTextEntity(BaseGroupTextEntity):
    """Text entity for editing sensors list."""

    _attr_name = "Sensors"

    def __init__(
        self,
        hass: HomeAssistant,
        manager: "IntegrationManager",
        group_name: str,
        config_dict: Dict,
    ) -> None:
        """Initialize sensors text entity."""
        super().__init__(hass, manager, group_name, config_dict)
        self._attr_unique_id = f"{DOMAIN}_{group_name}_sensors"
        self._update_native_value()

    def _update_native_value(self) -> None:
        """Update native value from config."""
        sensors = self._config_dict.get(CONF_SENSORS, [])
        self._attr_native_value = ", ".join(sensors)

    async def async_set_value(self, value: str) -> None:
        """Handle value change from UI."""
        sensors = [s.strip() for s in value.split(",") if s.strip()]
        new_config = dict(self._config_dict)
        new_config[CONF_SENSORS] = sensors
        
        _LOGGER.info(f"Updating sensors for group '{self._group_name}': {sensors}")
        await self._manager.update_group_config(self._group_name, new_config)


class TargetsTextEntity(BaseGroupTextEntity):
    """Text entity for editing targets list."""

    _attr_name = "Targets"

    def __init__(
        self,
        hass: HomeAssistant,
        manager: "IntegrationManager",
        group_name: str,
        config_dict: Dict,
    ) -> None:
        """Initialize targets text entity."""
        super().__init__(hass, manager, group_name, config_dict)
        self._attr_unique_id = f"{DOMAIN}_{group_name}_targets"
        self._update_native_value()

    def _update_native_value(self) -> None:
        """Update native value from config."""
        targets = self._config_dict.get(CONF_TARGETS, [])
        self._attr_native_value = ", ".join(targets)

    async def async_set_value(self, value: str) -> None:
        """Handle value change from UI."""
        targets = [t.strip() for t in value.split(",") if t.strip()]
        new_config = dict(self._config_dict)
        new_config[CONF_TARGETS] = targets
        
        _LOGGER.info(f"Updating targets for group '{self._group_name}': {targets}")
        await self._manager.update_group_config(self._group_name, new_config)


class DelayTextEntity(BaseGroupTextEntity):
    """Text entity for editing delay (can be template)."""

    _attr_name = "Delay"

    def __init__(
        self,
        hass: HomeAssistant,
        manager: "IntegrationManager",
        group_name: str,
        config_dict: Dict,
    ) -> None:
        """Initialize delay text entity."""
        super().__init__(hass, manager, group_name, config_dict)
        self._attr_unique_id = f"{DOMAIN}_{group_name}_delay"
        self._update_native_value()

    def _update_native_value(self) -> None:
        """Update native value from config."""
        delay = self._config_dict.get(CONF_DELAY, 0)
        self._attr_native_value = str(delay)

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


class GroupTextEntities:
    """Container for all text entities of a group."""

    def __init__(
        self,
        hass: HomeAssistant,
        manager: "IntegrationManager",
        group_name: str,
        config_dict: Dict,
    ) -> None:
        """Initialize group text entities."""
        self.sensors = SensorsTextEntity(hass, manager, group_name, config_dict)
        self.targets = TargetsTextEntity(hass, manager, group_name, config_dict)
        self.delay = DelayTextEntity(hass, manager, group_name, config_dict)

    def get_all(self) -> list:
        """Return all entities as a list."""
        return [self.sensors, self.targets, self.delay]

    def update_config(self, config_dict: Dict) -> None:
        """Update all entities with new config."""
        self.sensors.update_config(config_dict)
        self.targets.update_config(config_dict)
        self.delay.update_config(config_dict)
