"""Config flow for Auto Off integration."""

import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import CONF_GROUPS, CONF_POLL_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 15


class AutoOffConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Auto Off."""

    VERSION = 4

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
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL): vol.All(
                        vol.Coerce(int), vol.Range(min=5, max=300)
                    ),
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return AutoOffOptionsFlow()


class AutoOffOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Auto Off."""

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            # Update the config entry data with new poll_interval
            new_data = dict(self.config_entry.data)
            new_data[CONF_POLL_INTERVAL] = user_input[CONF_POLL_INTERVAL]
            self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
            return self.async_create_entry(title="", data={})

        current_poll_interval = self.config_entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_POLL_INTERVAL, default=current_poll_interval): vol.All(
                        vol.Coerce(int), vol.Range(min=5, max=300)
                    ),
                }
            ),
        )
