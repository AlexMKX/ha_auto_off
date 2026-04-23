"""Auto Off integration for Home Assistant."""
import logging

import voluptuous as vol
from pydantic import ValidationError

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import (
    DOMAIN,
    CONF_GROUPS,
    CONF_GROUP_NAME,
    CONF_SENSORS,
    CONF_SENSOR_TEMPLATES,
    CONF_TARGETS,
    CONF_DELAY,
    SERVICE_SET_GROUP,
    SERVICE_DELETE_GROUP,
    PLATFORMS,
)
from .integration_manager import IntegrationManager
from .auto_off import GroupConfig

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_GROUP_SCHEMA = vol.Schema({
    vol.Required(CONF_GROUP_NAME): cv.string,
    vol.Required(CONF_TARGETS): vol.All(cv.ensure_list, [cv.entity_id]),
    vol.Optional(CONF_SENSORS, default=list): vol.All(cv.ensure_list, [cv.entity_id]),
    vol.Optional(CONF_SENSOR_TEMPLATES, default=list): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional(CONF_DELAY, default=0): vol.Any(int, cv.string),
})

SERVICE_DELETE_GROUP_SCHEMA = vol.Schema({
    vol.Required(CONF_GROUP_NAME): cv.string,
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Auto Off from a config entry."""
    manager = IntegrationManager(hass, entry)
    hass.data[DOMAIN] = manager
    await manager.async_initialize()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await _async_register_services(hass, entry)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.services.async_remove(DOMAIN, SERVICE_SET_GROUP)
        hass.services.async_remove(DOMAIN, SERVICE_DELETE_GROUP)

        manager = hass.data.pop(DOMAIN, None)
        if manager is not None:
            await manager.async_unload()

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle config entry migration for auto_off.

    Version 3 introduces a breaking change to the group payload shape
    (structured fields instead of a YAML string) and moves occupancy
    sensors to the separate door_occupancy integration. Auto-migration
    is intentionally not implemented; users are asked to delete the
    old entry and recreate groups via the new set_group service.
    See docs/superpowers/specs/2026-04-22-split-integrations-design.md
    section 'Migration (for users)'.
    """
    if entry.version >= 3:
        return True

    _LOGGER.error(
        "Auto Off config entry at version %s requires manual migration. "
        "Delete this integration entry and reinstall per the README "
        "migration section.",
        entry.version,
    )
    return False


async def _async_register_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register Auto Off services."""

    async def handle_set_group(call: ServiceCall) -> None:
        """Create or update an auto-off group from structured service data."""
        group_name = call.data[CONF_GROUP_NAME]
        config_dict = {
            CONF_TARGETS: list(call.data[CONF_TARGETS]),
            CONF_SENSORS: list(call.data.get(CONF_SENSORS, [])),
            CONF_SENSOR_TEMPLATES: list(call.data.get(CONF_SENSOR_TEMPLATES, [])),
            CONF_DELAY: call.data.get(CONF_DELAY, 0),
        }

        try:
            GroupConfig.model_validate(config_dict)
        except ValidationError as err:
            _LOGGER.error("Invalid config for group '%s': %s", group_name, err.errors())
            return

        current_groups = dict(entry.data.get(CONF_GROUPS, {}))
        is_new_group = group_name not in current_groups
        current_groups[group_name] = config_dict

        new_data = dict(entry.data)
        new_data[CONF_GROUPS] = current_groups
        hass.config_entries.async_update_entry(entry, data=new_data)

        manager = hass.data.get(DOMAIN)
        if manager is None:
            _LOGGER.error("Integration manager not found")
            return

        await manager.set_group(group_name, config_dict, is_new_group)
        _LOGGER.info(
            "Group '%s' %s", group_name, "created" if is_new_group else "updated"
        )

    async def handle_delete_group(call: ServiceCall) -> None:
        """Delete an auto-off group."""
        group_name = call.data[CONF_GROUP_NAME]

        current_groups = dict(entry.data.get(CONF_GROUPS, {}))
        if group_name not in current_groups:
            _LOGGER.warning("Group '%s' does not exist", group_name)
            return

        del current_groups[group_name]
        new_data = dict(entry.data)
        new_data[CONF_GROUPS] = current_groups
        hass.config_entries.async_update_entry(entry, data=new_data)

        manager = hass.data.get(DOMAIN)
        if manager is None:
            _LOGGER.error("Integration manager not found")
            return

        await manager.delete_group(group_name)
        _LOGGER.info("Group '%s' deleted", group_name)

    # Register services
    hass.services.async_register(
        DOMAIN, SERVICE_SET_GROUP, handle_set_group, schema=SERVICE_SET_GROUP_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_GROUP, handle_delete_group, schema=SERVICE_DELETE_GROUP_SCHEMA
    )


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Remove a device from the integration via UI."""
    # Extract group name from device identifiers
    group_name = None
    for identifier in device_entry.identifiers:
        if identifier[0] == DOMAIN:
            group_name = identifier[1]
            break

    if not group_name:
        _LOGGER.warning(f"Could not find group name for device {device_entry.id}")
        return False

    try:
        # Get current groups from config entry
        current_groups = dict(config_entry.data.get(CONF_GROUPS, {}))

        if group_name not in current_groups:
            _LOGGER.warning(f"Group '{group_name}' not found in config")
            return True  # Device can be removed anyway

        # Remove group from config
        del current_groups[group_name]

        # Update config entry
        new_data = dict(config_entry.data)
        new_data[CONF_GROUPS] = current_groups
        hass.config_entries.async_update_entry(config_entry, data=new_data)

        # Get manager and remove group
        manager = hass.data.get(DOMAIN)
        if manager:
            await manager.delete_group(group_name)
            _LOGGER.info(f"Group '{group_name}' deleted via UI")

        return True

    except Exception as e:
        _LOGGER.exception(f"Failed to remove device for group '{group_name}': {e}")
        return False
