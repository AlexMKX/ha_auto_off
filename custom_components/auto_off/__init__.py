"""Auto Off integration for Home Assistant."""
import logging
import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    CONF_GROUPS,
    CONF_GROUP_NAME,
    CONF_SENSORS,
    CONF_TARGETS,
    CONF_DELAY,
    SERVICE_SET_GROUP,
    SERVICE_DELETE_GROUP,
    PLATFORMS,
)
from .integration_manager import async_unload_integration
from .auto_off import AutoOffManager

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_GROUP_SCHEMA = vol.Schema({
    vol.Required(CONF_GROUP_NAME): cv.string,
    vol.Required(CONF_SENSORS): vol.All(cv.ensure_list, [cv.entity_id]),
    vol.Required(CONF_TARGETS): vol.All(cv.ensure_list, [cv.entity_id]),
    vol.Optional(CONF_DELAY, default=0): vol.Any(cv.positive_int, cv.string),
})

SERVICE_DELETE_GROUP_SCHEMA = vol.Schema({
    vol.Required(CONF_GROUP_NAME): cv.string,
})


async def async_setup(hass: HomeAssistant, config):
    """Set up the Auto Off component from YAML (legacy support)."""
    conf = config.get(DOMAIN)
    if conf is not None:
        hass.data.setdefault(DOMAIN, {})
        hass.data[DOMAIN]["yaml_config"] = conf
        # Create config entry from YAML if it doesn't exist
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data=conf
            )
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Auto Off from a config entry."""
    # Forward setup to platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    await _async_register_services(hass, entry)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Unregister services
        hass.services.async_remove(DOMAIN, SERVICE_SET_GROUP)
        hass.services.async_remove(DOMAIN, SERVICE_DELETE_GROUP)

        await async_unload_integration(hass, entry)

    return unload_ok


async def _async_register_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register Auto Off services."""

    async def handle_set_group(call: ServiceCall) -> None:
        """Handle set_group service call."""
        group_name = call.data[CONF_GROUP_NAME]
        sensors = call.data[CONF_SENSORS]
        targets = call.data[CONF_TARGETS]
        delay = call.data.get(CONF_DELAY, 0)

        try:
            # Build config dict
            config_dict = {
                CONF_SENSORS: sensors,
                CONF_TARGETS: targets,
                CONF_DELAY: delay,
            }

            # Get current groups from config entry
            current_groups = dict(entry.data.get(CONF_GROUPS, {}))
            is_new_group = group_name not in current_groups

            # Update groups with structured data
            current_groups[group_name] = config_dict

            # Update config entry
            new_data = dict(entry.data)
            new_data[CONF_GROUPS] = current_groups
            hass.config_entries.async_update_entry(entry, data=new_data)

            # Get manager and update/create group
            manager = hass.data.get(DOMAIN)
            if manager:
                await manager.set_group(group_name, config_dict, is_new_group)
                _LOGGER.info(f"Group '{group_name}' {'created' if is_new_group else 'updated'}")
            else:
                _LOGGER.error("Integration manager not found")

        except Exception as e:
            _LOGGER.exception(f"Failed to set group '{group_name}': {e}")

    async def handle_delete_group(call: ServiceCall) -> None:
        """Handle delete_group service call."""
        group_name = call.data[CONF_GROUP_NAME]

        try:
            # Get current groups from config entry
            current_groups = dict(entry.data.get(CONF_GROUPS, {}))

            if group_name not in current_groups:
                _LOGGER.warning(f"Group '{group_name}' does not exist")
                return

            # Remove group
            del current_groups[group_name]

            # Update config entry
            new_data = dict(entry.data)
            new_data[CONF_GROUPS] = current_groups
            hass.config_entries.async_update_entry(entry, data=new_data)

            # Get manager and remove group
            manager = hass.data.get(DOMAIN)
            if manager:
                await manager.delete_group(group_name)
                _LOGGER.info(f"Group '{group_name}' deleted")
            else:
                _LOGGER.error("Integration manager not found")

        except Exception as e:
            _LOGGER.exception(f"Failed to delete group '{group_name}': {e}")

    # Register services
    hass.services.async_register(
        DOMAIN, SERVICE_SET_GROUP, handle_set_group, schema=SERVICE_SET_GROUP_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_GROUP, handle_delete_group, schema=SERVICE_DELETE_GROUP_SCHEMA
    )
