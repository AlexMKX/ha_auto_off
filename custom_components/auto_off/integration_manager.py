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
from .group_entities import (
    TARGET_GROUP_ENTITY_CLASSES,
    AutoOffSensorsGroup,
    sensors_group_entity_id,
    split_targets_by_domain,
    targets_group_entity_id,
)

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
        # Per-platform AddEntitiesCallback captured from each platform's async_setup_entry
        self._platform_callbacks: dict[str, Any] = {}
        # Live sensors-group entities, keyed by group_name
        self._sensors_group_entities: dict[str, AutoOffSensorsGroup] = {}
        # Live per-domain targets-group entities: (group_name, domain) -> entity instance
        self._targets_group_entities: dict[tuple[str, str], Any] = {}

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

    def register_platform_callback(
        self, platform: str, async_add_entities: AddEntitiesCallback
    ) -> None:
        """Store the AddEntitiesCallback for a forwarded platform.

        Called once per platform from that platform's async_setup_entry.
        On first-time registration we retroactively emit all already-known
        group entities for that platform so they appear immediately instead
        of waiting for the next set_group call.
        """
        self._platform_callbacks[platform] = async_add_entities
        self._emit_initial_entities_for_platform(platform)

    def _emit_initial_entities_for_platform(self, platform: str) -> None:
        """Create entities on this platform for every already-configured group."""
        async_add_entities = self._platform_callbacks.get(platform)
        if async_add_entities is None:
            return
        new_entities = []
        for group_name, config_dict in self._groups_data.items():
            config = GroupConfig.model_validate(config_dict)
            if platform == "binary_sensor":
                if group_name in self._sensors_group_entities:
                    continue
                entity = AutoOffSensorsGroup(
                    group_name=group_name,
                    entity_ids=list(config.sensors),
                    sensor_templates=list(config.sensor_templates),
                )
                self._sensors_group_entities[group_name] = entity
                new_entities.append(entity)
            elif platform in TARGET_GROUP_ENTITY_CLASSES:
                buckets = split_targets_by_domain(list(config.targets))
                ids = buckets.get(platform, [])
                if not ids:
                    continue
                key = (group_name, platform)
                if key in self._targets_group_entities:
                    continue
                cls = TARGET_GROUP_ENTITY_CLASSES[platform]
                entity = cls(group_name=group_name, entity_ids=ids)
                self._targets_group_entities[key] = entity
                new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    def get_group_targets_by_domain(self, group_name: str) -> dict[str, list[str]]:
        """Return {domain: [entity_id, ...]} for groupable targets of a group."""
        config = self.get_group_config(group_name)
        if config is None:
            return {}
        return split_targets_by_domain(list(config.targets))

    def get_group_member_group_entity_ids(self, group_name: str) -> list[str]:
        """Return the entity_ids of all live targets-group entities for a group.

        Used by SensorGroup.turn_off to dispatch one <domain>.turn_off call
        per domain-group.
        """
        return [
            targets_group_entity_id(domain, group_name)
            for (name, domain) in self._targets_group_entities.keys()
            if name == group_name
        ]

    async def _sync_group_entities(
        self, group_name: str, config_dict: dict, is_new: bool
    ) -> None:
        """Create/update/remove group entities to match config_dict.

        Safe to call whether or not the platform callbacks are registered —
        missing callbacks mean we skip emission and will retry on
        register_platform_callback.
        """
        config = GroupConfig.model_validate(config_dict)

        # Sensors-group
        sensors_cb = self._platform_callbacks.get("binary_sensor")
        sensors_entity = self._sensors_group_entities.get(group_name)
        if sensors_entity is None:
            entity = AutoOffSensorsGroup(
                group_name=group_name,
                entity_ids=list(config.sensors),
                sensor_templates=list(config.sensor_templates),
            )
            self._sensors_group_entities[group_name] = entity
            if sensors_cb is not None:
                sensors_cb([entity])
        else:
            sensors_entity.update_members(
                entity_ids=list(config.sensors),
                sensor_templates=list(config.sensor_templates),
            )
            if sensors_entity.hass is not None:
                sensors_entity.async_write_ha_state()

        # Targets-groups per domain
        desired = split_targets_by_domain(list(config.targets))
        current_domains = {
            domain for (gname, domain) in self._targets_group_entities if gname == group_name
        }
        desired_domains = set(desired.keys())

        # Remove domains that are no longer present
        gone_domains = current_domains - desired_domains
        if gone_domains:
            ent_reg = er.async_get(self.hass)
            for gone_domain in gone_domains:
                key = (group_name, gone_domain)
                entity = self._targets_group_entities.pop(key, None)
                if entity is None:
                    continue
                entity_id = targets_group_entity_id(gone_domain, group_name)
                ent_reg.async_remove(entity_id)

        # Add / update domains that should exist
        for domain, ids in desired.items():
            key = (group_name, domain)
            existing = self._targets_group_entities.get(key)
            cb = self._platform_callbacks.get(domain)
            if existing is None:
                cls = TARGET_GROUP_ENTITY_CLASSES[domain]
                entity = cls(group_name=group_name, entity_ids=ids)
                self._targets_group_entities[key] = entity
                if cb is not None:
                    cb([entity])
            else:
                existing.update_members(entity_ids=ids)
                if existing.hass is not None:
                    existing.async_write_ha_state()

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

            # --- sync group entities ---
            await self._sync_group_entities(group_name, config_dict, is_new)

        except Exception as e:
            _LOGGER.exception(f"Failed to set group '{group_name}': {e}")
            raise

    def get_group_config(self, group_name: str) -> GroupConfig | None:
        """Return the active GroupConfig for a group, or None if absent."""
        config_dict = self._groups_data.get(group_name)
        if config_dict is None:
            return None
        try:
            return GroupConfig.model_validate(config_dict)
        except Exception as exc:  # noqa: BLE001 — defensive; manager must not crash
            _LOGGER.warning("Invalid stored config for group '%s': %s", group_name, exc)
            return None

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

            # Remove sensors-group entity
            ent_reg = er.async_get(self.hass)
            sensors_entity = self._sensors_group_entities.pop(group_name, None)
            if sensors_entity is not None and ent_reg:
                ent_reg.async_remove(sensors_group_entity_id(group_name))

            # Remove every per-domain targets-group entity
            for key in [k for k in self._targets_group_entities if k[0] == group_name]:
                self._targets_group_entities.pop(key, None)
                _, domain = key
                if ent_reg:
                    ent_reg.async_remove(targets_group_entity_id(domain, group_name))

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
