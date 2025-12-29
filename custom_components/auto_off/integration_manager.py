"""Integration manager for Auto Off."""
import asyncio
import yaml
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er, device_registry as dr
from datetime import timedelta
import logging
from typing import Dict, Optional, Any

from .auto_off import AutoOffManager, GroupConfig
from .door_occupancy import DoorOccupancyManager
from .const import DOMAIN, CONF_GROUPS, CONF_POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 15


def parse_group_configs(groups_data: Dict[str, str]) -> Dict[str, GroupConfig]:
    """Parse YAML strings into GroupConfig objects."""
    result = {}
    for group_name, config_yaml in groups_data.items():
        try:
            config_dict = yaml.safe_load(config_yaml)
            if isinstance(config_dict, dict):
                result[group_name] = GroupConfig.model_validate(config_dict)
        except Exception as e:
            _LOGGER.error(f"Failed to parse config for group '{group_name}': {e}")
    return result


class IntegrationManager:
    """Manages the Auto Off integration."""

    def __init__(self, hass, entry, async_add_entities):
        self.hass = hass
        self.entry = entry
        self._binary_sensor_async_add_entities = async_add_entities
        self._text_async_add_entities: Optional[AddEntitiesCallback] = None
        self._text_entities: Dict[str, Any] = {}
        
        # Parse groups from config entry
        groups_data = entry.data.get(CONF_GROUPS, {})
        group_configs = parse_group_configs(groups_data)
        
        self.auto_off = AutoOffManager(hass, group_configs)
        self.door_occupancy = DoorOccupancyManager(hass, entry)
        self._lock = asyncio.Lock()
        self._remove_listener = None
        self._groups_yaml: Dict[str, str] = dict(groups_data)

    def text_platform_ready(self, async_add_entities: AddEntitiesCallback) -> None:
        """Called when text platform is ready."""
        self._text_async_add_entities = async_add_entities
        # Create text entities for existing groups
        self._create_text_entities_for_existing_groups()

    def _create_text_entities_for_existing_groups(self) -> None:
        """Create text entities for all existing groups."""
        if not self._text_async_add_entities:
            return

        from .text import GroupConfigTextEntity

        new_entities = []
        for group_name, config_yaml in self._groups_yaml.items():
            if group_name not in self._text_entities:
                entity = GroupConfigTextEntity(
                    self.hass, self.entry, group_name, config_yaml
                )
                self._text_entities[group_name] = entity
                new_entities.append(entity)

        if new_entities:
            self._text_async_add_entities(new_entities)
            _LOGGER.info(f"Created {len(new_entities)} text entities for groups")

    async def async_initialize(self):
        """Initialize the integration manager."""
        # Initialize door_occupancy with async_add_entities
        self.door_occupancy._async_add_entities = self._binary_sensor_async_add_entities
        await self.door_occupancy._discover_and_add_sensors()

        poll_interval = self.entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        self._remove_listener = async_track_time_interval(
            self.hass, self._periodic_worker, timedelta(seconds=poll_interval)
        )
        _LOGGER.info(f"IntegrationManager initialized with poll_interval {poll_interval}s")

    async def _periodic_worker(self, now):
        """Periodic worker for checking states."""
        if self._lock.locked():
            _LOGGER.warning("IntegrationManager worker already running, skipping this tick")
            return
        async with self._lock:
            await self.auto_off.periodic_worker()
            await self.door_occupancy.periodic_discovery()

    async def set_group(self, group_name: str, config_yaml: str, is_new: bool) -> None:
        """Create or update a group."""
        try:
            config_dict = yaml.safe_load(config_yaml)
            group_config = GroupConfig.model_validate(config_dict)

            # Update internal state
            self._groups_yaml[group_name] = config_yaml

            # Update AutoOffManager
            self.auto_off.config[group_name] = group_config
            self.auto_off._init_groups()

            # Create or update text entity
            if is_new and self._text_async_add_entities:
                from .text import GroupConfigTextEntity
                entity = GroupConfigTextEntity(
                    self.hass, self.entry, group_name, config_yaml
                )
                self._text_entities[group_name] = entity
                self._text_async_add_entities([entity])
                _LOGGER.info(f"Created text entity for new group '{group_name}'")
            elif group_name in self._text_entities:
                self._text_entities[group_name].update_config(config_yaml)

        except Exception as e:
            _LOGGER.exception(f"Failed to set group '{group_name}': {e}")
            raise

    async def update_group_config(self, group_name: str, config_yaml: str) -> None:
        """Update group config from text entity edit."""
        await self.set_group(group_name, config_yaml, is_new=False)

        # Also update config entry
        current_groups = dict(self.entry.data.get(CONF_GROUPS, {}))
        current_groups[group_name] = config_yaml
        new_data = dict(self.entry.data)
        new_data[CONF_GROUPS] = current_groups
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    async def delete_group(self, group_name: str) -> None:
        """Delete a group."""
        try:
            # Remove from internal state
            if group_name in self._groups_yaml:
                del self._groups_yaml[group_name]

            # Remove from AutoOffManager
            if group_name in self.auto_off.config:
                # Unload the group first
                if group_name in self.auto_off._groups:
                    await self.auto_off._groups[group_name].async_unload()
                    del self.auto_off._groups[group_name]
                del self.auto_off.config[group_name]

            # Remove text entity
            if group_name in self._text_entities:
                entity = self._text_entities.pop(group_name)
                # Remove entity from HA
                ent_reg = er.async_get(self.hass)
                if ent_reg and entity.entity_id:
                    ent_reg.async_remove(entity.entity_id)

            # Remove device
            dev_reg = dr.async_get(self.hass)
            if dev_reg:
                device = dev_reg.async_get_device(identifiers={(DOMAIN, group_name)})
                if device:
                    dev_reg.async_remove_device(device.id)

            _LOGGER.info(f"Group '{group_name}' deleted successfully")

        except Exception as e:
            _LOGGER.exception(f"Failed to delete group '{group_name}': {e}")
            raise

    async def async_unload(self):
        """Unload the integration manager."""
        if self._remove_listener:
            self._remove_listener()
            self._remove_listener = None
        await self.auto_off.async_unload()
        await self.door_occupancy.async_unload()
        self._text_entities.clear()


default_manager = None


async def async_setup_integration(hass, entry, async_add_entities):
    """Set up the integration from binary_sensor platform."""
    global default_manager
    manager = IntegrationManager(hass, entry, async_add_entities)
    hass.data[DOMAIN] = manager
    default_manager = manager
    await manager.async_initialize()
    return True


async def async_unload_integration(hass, entry):
    """Unload the integration."""
    global default_manager
    manager = hass.data.pop(DOMAIN, None)
    if manager and hasattr(manager, "async_unload"):
        await manager.async_unload()
    default_manager = None
    return True