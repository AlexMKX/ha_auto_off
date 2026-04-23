"""Integration manager for Auto Off."""

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .auto_off import AutoOffManager, GroupConfig
from .const import CONF_GROUPS, CONF_POLL_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 15


def parse_group_configs(groups_data: dict[str, dict]) -> dict[str, GroupConfig]:
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

    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self._sensor_async_add_entities: AddEntitiesCallback | None = None
        self._deadline_entities: dict[str, Any] = {}
        self._text_async_add_entities: AddEntitiesCallback | None = None
        self._text_entities: dict[str, Any] = {}

        # Parse groups from config entry
        groups_data = entry.data.get(CONF_GROUPS, {})
        group_configs = parse_group_configs(groups_data)

        self.auto_off = AutoOffManager(
            hass,
            group_configs,
            on_deadline_change=self._on_deadline_change,
        )
        self._lock = asyncio.Lock()
        self._remove_listener = None
        self._groups_data: dict[str, dict] = dict(groups_data)

    def _on_deadline_change(self, group_name: str, deadline_iso: str | None) -> None:
        deadline_entity = self._deadline_entities.get(group_name)
        if not deadline_entity:
            return
        deadline_entity.update_deadline(deadline_iso)

    def sensor_platform_ready(self, async_add_entities: AddEntitiesCallback) -> None:
        """Register the sensor platform's add-entities callback and create
        deadline sensors for any groups that already exist in the config entry."""
        from .sensor import DeadlineSensorEntity

        self._sensor_async_add_entities = async_add_entities

        new_entities = []
        for group_name in self._groups_data:
            if group_name in self._deadline_entities:
                continue
            deadline_entity = DeadlineSensorEntity(self.hass, self.entry, group_name, self)
            self._deadline_entities[group_name] = deadline_entity
            new_entities.append(deadline_entity)

        if new_entities:
            async_add_entities(new_entities)
            _LOGGER.info("Created %d deadline sensor entities for groups", len(new_entities))

    def text_platform_ready(self, async_add_entities: AddEntitiesCallback) -> None:
        """Register the text platform's add-entities callback and create
        delay text entities for any groups that already exist in the config entry."""
        from .text import DelayTextEntity

        self._text_async_add_entities = async_add_entities

        new_entities = []
        for group_name, config_dict in self._groups_data.items():
            if group_name in self._text_entities:
                continue
            delay_entity = DelayTextEntity(self.hass, self, group_name, config_dict)
            self._text_entities[group_name] = delay_entity
            new_entities.append(delay_entity)

        if new_entities:
            async_add_entities(new_entities)
            _LOGGER.info("Created %d delay text entities for groups", len(new_entities))

    async def async_initialize(self):
        """Initialize the integration manager."""
        # Initialize groups (awaits unload of any old groups)
        await self.auto_off.async_init_groups()

        poll_interval = self.entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        self._remove_listener = async_track_time_interval(
            self.hass, self._periodic_worker, timedelta(seconds=poll_interval)
        )
        _LOGGER.info("IntegrationManager initialized with poll_interval %ds", poll_interval)

    async def _periodic_worker(self, now):
        """Periodic worker: advance group state machines and refresh
        deadline sensors with the latest human-readable deadline."""
        if self._lock.locked():
            _LOGGER.warning("IntegrationManager worker already running, skipping this tick")
            return
        async with self._lock:
            await self.auto_off.periodic_worker()
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

    async def set_group(self, group_name: str, config_dict: dict, is_new: bool) -> None:
        """Create or update a group."""
        try:
            group_config = GroupConfig.model_validate(config_dict)

            # Update internal state
            self._groups_data[group_name] = config_dict

            # Update AutoOffManager (awaits unload of old groups)
            self.auto_off.config[group_name] = group_config
            await self.auto_off.async_init_groups()

            # Trigger immediate state check for new group
            if is_new:
                group = self.auto_off._groups.get(group_name)
                if group:
                    await group.check_and_set_deadline()

            # Create or update sensor entity
            if is_new and self._sensor_async_add_entities:
                from .sensor import DeadlineSensorEntity

                deadline_entity = DeadlineSensorEntity(self.hass, self.entry, group_name, self)
                self._deadline_entities[group_name] = deadline_entity
                self._sensor_async_add_entities([deadline_entity])
                _LOGGER.info("Created deadline sensor for new group '%s'", group_name)

                self._update_deadline_sensor_for_group(group_name)

            # Create or update delay text entity
            if is_new and self._text_async_add_entities:
                from .text import DelayTextEntity

                delay_entity = DelayTextEntity(self.hass, self, group_name, config_dict)
                self._text_entities[group_name] = delay_entity
                self._text_async_add_entities([delay_entity])
                _LOGGER.info(f"Created delay text entity for new group '{group_name}'")
            elif group_name in self._text_entities:
                self._text_entities[group_name].update_config(config_dict)

            # Refresh attributes on the deadline sensor so the UI reflects the
            # new group config immediately.
            deadline_entity = self._deadline_entities.get(group_name)
            if deadline_entity is not None and not is_new:
                deadline_entity.async_write_ha_state()

        except Exception as e:
            _LOGGER.exception(f"Failed to set group '{group_name}': {e}")
            raise

    def get_group_config(self, group_name: str) -> GroupConfig | None:
        """Return the active GroupConfig for a group, or None during teardown."""
        group = self.auto_off._groups.get(group_name)
        if group is None:
            return None
        return group._config

    async def update_group_config(self, group_name: str, config_dict: dict) -> None:
        """Update group config from text entity edit or set_group service."""
        await self.set_group(group_name, config_dict, is_new=False)

        # Also update config entry
        current_groups = dict(self.entry.data.get(CONF_GROUPS, {}))
        current_groups[group_name] = config_dict
        new_data = dict(self.entry.data)
        new_data[CONF_GROUPS] = current_groups
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

        # Refresh UI attributes on the deadline sensor.
        deadline_entity = self._deadline_entities.get(group_name)
        if deadline_entity is not None:
            deadline_entity.async_write_ha_state()

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
        self._deadline_entities.clear()
        self._text_entities.clear()
