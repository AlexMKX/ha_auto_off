"""Integration manager for Auto Off."""
import asyncio
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


def parse_group_configs(groups_data: Dict[str, Dict]) -> Dict[str, GroupConfig]:
    """Parse structured dicts into GroupConfig objects."""
    result = {}
    for group_name, config_dict in groups_data.items():
        try:
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
        self._sensor_async_add_entities: Optional[AddEntitiesCallback] = None
        self._sensor_entities: Dict[str, Any] = {}
        self._deadline_entities: Dict[str, Any] = {}
        self._text_async_add_entities: Optional[AddEntitiesCallback] = None
        self._text_entities: Dict[str, Any] = {}
        
        # Parse groups from config entry
        groups_data = entry.data.get(CONF_GROUPS, {})
        group_configs = parse_group_configs(groups_data)
        
        self.auto_off = AutoOffManager(
            hass, group_configs,
            on_deadline_change=self._update_deadline_sensor_for_group
        )
        self.door_occupancy = DoorOccupancyManager(hass, entry)
        self._lock = asyncio.Lock()
        self._remove_listener = None
        self._groups_data: Dict[str, Dict] = dict(groups_data)

    def sensor_platform_ready(self, async_add_entities: AddEntitiesCallback) -> None:
        """Called when sensor platform is ready."""
        self._sensor_async_add_entities = async_add_entities
        # Create sensor entities for existing groups
        self._create_sensor_entities_for_existing_groups()

    def _create_sensor_entities_for_existing_groups(self) -> None:
        """Create sensor entities for all existing groups."""
        if not self._sensor_async_add_entities:
            return

        from .sensor import GroupConfigSensorEntity, DeadlineSensorEntity

        new_entities = []
        for group_name, config_dict in self._groups_data.items():
            if group_name not in self._sensor_entities:
                entity = GroupConfigSensorEntity(
                    self.hass, self.entry, group_name, config_dict
                )
                self._sensor_entities[group_name] = entity
                new_entities.append(entity)
            
            if group_name not in self._deadline_entities:
                deadline_entity = DeadlineSensorEntity(
                    self.hass, self.entry, group_name
                )
                self._deadline_entities[group_name] = deadline_entity
                new_entities.append(deadline_entity)

        if new_entities:
            self._sensor_async_add_entities(new_entities)
            _LOGGER.info(f"Created {len(new_entities)} sensor entities for groups")

    def text_platform_ready(self, async_add_entities: AddEntitiesCallback) -> None:
        """Called when text platform is ready."""
        self._text_async_add_entities = async_add_entities
        self._create_text_entities_for_existing_groups()

    def _create_text_entities_for_existing_groups(self) -> None:
        """Create text entities for all existing groups."""
        if not self._text_async_add_entities:
            return

        from .text import DelayTextEntity

        new_entities = []
        for group_name, config_dict in self._groups_data.items():
            if group_name not in self._text_entities:
                delay_entity = DelayTextEntity(
                    self.hass, self, group_name, config_dict
                )
                self._text_entities[group_name] = delay_entity
                new_entities.append(delay_entity)

        if new_entities:
            self._text_async_add_entities(new_entities)
            _LOGGER.info(f"Created {len(new_entities)} delay text entities for groups")

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
            self._update_deadline_sensors()

    def _update_deadline_sensors(self) -> None:
        """Update all deadline sensors with current deadline values."""
        for group_name in self._deadline_entities:
            self._update_deadline_sensor_for_group(group_name)

    def _update_deadline_sensor_for_group(self, group_name: str) -> None:
        """Update deadline sensor for a specific group."""
        deadline_entity = self._deadline_entities.get(group_name)
        if not deadline_entity:
            return
        
        group = self.auto_off._groups.get(group_name)
        if group:
            deadline_str = group._get_human_deadline()
            if deadline_str == "None":
                deadline_entity.update_deadline(None)
            else:
                deadline_entity.update_deadline(deadline_str)

    async def set_group(self, group_name: str, config_dict: Dict, is_new: bool) -> None:
        """Create or update a group."""
        try:
            group_config = GroupConfig.model_validate(config_dict)

            # Update internal state
            self._groups_data[group_name] = config_dict

            # Update AutoOffManager
            self.auto_off.config[group_name] = group_config
            self.auto_off._init_groups()
            
            # Trigger immediate state check for new group
            if is_new:
                group = self.auto_off._groups.get(group_name)
                if group:
                    await group.check_and_set_deadline()

            # Create or update sensor entity
            if is_new and self._sensor_async_add_entities:
                from .sensor import GroupConfigSensorEntity, DeadlineSensorEntity
                new_sensors = []
                
                entity = GroupConfigSensorEntity(
                    self.hass, self.entry, group_name, config_dict
                )
                self._sensor_entities[group_name] = entity
                new_sensors.append(entity)
                
                deadline_entity = DeadlineSensorEntity(
                    self.hass, self.entry, group_name
                )
                self._deadline_entities[group_name] = deadline_entity
                new_sensors.append(deadline_entity)
                
                self._sensor_async_add_entities(new_sensors)
                _LOGGER.info(f"Created sensor entities for new group '{group_name}'")
                
                # Immediately update deadline sensor with current deadline
                self._update_deadline_sensor_for_group(group_name)
            elif group_name in self._sensor_entities:
                self._sensor_entities[group_name].update_config(config_dict)

            # Create or update delay text entity
            if is_new and self._text_async_add_entities:
                from .text import DelayTextEntity
                delay_entity = DelayTextEntity(
                    self.hass, self, group_name, config_dict
                )
                self._text_entities[group_name] = delay_entity
                self._text_async_add_entities([delay_entity])
                _LOGGER.info(f"Created delay text entity for new group '{group_name}'")
            elif group_name in self._text_entities:
                self._text_entities[group_name].update_config(config_dict)

        except Exception as e:
            _LOGGER.exception(f"Failed to set group '{group_name}': {e}")
            raise

    async def update_group_config(self, group_name: str, config_dict: Dict) -> None:
        """Update group config from text entity edit."""
        await self.set_group(group_name, config_dict, is_new=False)

        # Also update config entry
        current_groups = dict(self.entry.data.get(CONF_GROUPS, {}))
        current_groups[group_name] = config_dict
        new_data = dict(self.entry.data)
        new_data[CONF_GROUPS] = current_groups
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    async def delete_group(self, group_name: str) -> None:
        """Delete a group."""
        try:
            # Remove from internal state
            if group_name in self._groups_data:
                del self._groups_data[group_name]

            # Remove from AutoOffManager
            if group_name in self.auto_off.config:
                # Unload the group first
                if group_name in self.auto_off._groups:
                    await self.auto_off._groups[group_name].async_unload()
                    del self.auto_off._groups[group_name]
                del self.auto_off.config[group_name]

            # Remove sensor entity
            if group_name in self._sensor_entities:
                entity = self._sensor_entities.pop(group_name)
                ent_reg = er.async_get(self.hass)
                if ent_reg and entity.entity_id:
                    ent_reg.async_remove(entity.entity_id)

            # Remove deadline entity
            if group_name in self._deadline_entities:
                entity = self._deadline_entities.pop(group_name)
                ent_reg = er.async_get(self.hass)
                if ent_reg and entity.entity_id:
                    ent_reg.async_remove(entity.entity_id)

            # Remove delay text entity
            if group_name in self._text_entities:
                entity = self._text_entities.pop(group_name)
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
        self._sensor_entities.clear()
        self._deadline_entities.clear()
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