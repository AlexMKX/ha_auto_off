"""Config flow for Auto Off integration."""
import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import DOMAIN, CONF_POLL_INTERVAL, CONF_GROUPS

_LOGGER = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 15


class AutoOffConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Auto Off."""

    VERSION = 2

    async def async_step_user(self, user_input=None):
        """Handle the initial step - just create the integration."""
        # Only allow one instance of the integration
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(
                title="Auto Off",
                data={
                    CONF_POLL_INTERVAL: user_input.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    CONF_GROUPS: {},
                },
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Optional(CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL): vol.All(
                    vol.Coerce(int), vol.Range(min=5, max=300)
                ),
            }),
        )

    async def async_step_import(self, import_config):
        """Handle import from YAML (legacy support)."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        # Convert YAML groups to new format
        groups = {}
        if "groups" in import_config:
            import yaml
            for group_name, group_config in import_config["groups"].items():
                groups[group_name] = yaml.dump(group_config, default_flow_style=False)

        return self.async_create_entry(
            title="Auto Off",
            data={
                CONF_POLL_INTERVAL: import_config.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                CONF_GROUPS: groups,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return AutoOffOptionsFlow(config_entry)


class AutoOffOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Auto Off."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            # Update the config entry data with new poll_interval
            new_data = dict(self.config_entry.data)
            new_data[CONF_POLL_INTERVAL] = user_input[CONF_POLL_INTERVAL]
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )
            return self.async_create_entry(title="", data={})

        current_poll_interval = self.config_entry.data.get(
            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_POLL_INTERVAL, 
                    default=current_poll_interval
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
            }),
        )