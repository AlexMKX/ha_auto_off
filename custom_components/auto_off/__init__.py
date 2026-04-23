"""Auto Off integration for Home Assistant."""

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from pydantic import ValidationError

from .auto_off import GroupConfig
from .const import (
    CONF_DELAY,
    CONF_GROUP_NAME,
    CONF_GROUPS,
    CONF_SENSOR_TEMPLATES,
    CONF_SENSORS,
    CONF_TARGETS,
    DOMAIN,
    PLATFORMS,
    SERVICE_DELETE_GROUP,
    SERVICE_SET_GROUP,
    VERSION,
)
from .integration_manager import IntegrationManager

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_GROUP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_GROUP_NAME): cv.string,
        vol.Required(CONF_TARGETS): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional(CONF_SENSORS, default=list): vol.All(cv.ensure_list, [cv.entity_id]),
        vol.Optional(CONF_SENSOR_TEMPLATES, default=list): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(CONF_DELAY, default=0): vol.Any(int, cv.string),
    }
)

SERVICE_DELETE_GROUP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_GROUP_NAME): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Auto Off from a config entry."""
    manager = IntegrationManager(hass, entry)
    hass.data[DOMAIN] = manager
    await manager.async_initialize()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await _async_register_services(hass, entry)

    # Refresh sw_version on existing devices so the UI shows the current
    # release after upgrade.  HA only records DeviceInfo fields on device
    # creation, so a field like sw_version becomes stale on upgrades unless
    # we explicitly push it to the registry here.
    dev_reg = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if device.sw_version == VERSION:
            continue
        dev_reg.async_update_device(device.id, sw_version=VERSION)

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

    v1 → v2: structured group fields (automatic, data shape unchanged).
    v2 → v3: no data change needed; only version bump.
    """
    if entry.version >= 3:
        return True

    if entry.version == 2:
        # v2 data already uses the same structured format as v3.
        # The only difference is the version number.
        _LOGGER.info("Migrating auto_off config entry from version 2 to 3 (data shape unchanged)")
        hass.config_entries.async_update_entry(entry, version=3)
        return True

    _LOGGER.error(
        "Auto Off config entry at version %s cannot be auto-migrated. " "Delete this integration entry and reinstall.",
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
        _LOGGER.info("Group '%s' %s", group_name, "created" if is_new_group else "updated")

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
    hass.services.async_register(DOMAIN, SERVICE_SET_GROUP, handle_set_group, schema=SERVICE_SET_GROUP_SCHEMA)
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_GROUP,
        handle_delete_group,
        schema=SERVICE_DELETE_GROUP_SCHEMA,
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
