"""Config flow for the Door Occupancy integration."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    CONF_OCCUPANCY_TIMEOUT,
    CONF_POLL_INTERVAL,
    DEFAULT_OCCUPANCY_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)


def _user_schema(
    current_poll: int = DEFAULT_POLL_INTERVAL,
    current_timeout: int = DEFAULT_OCCUPANCY_TIMEOUT,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_POLL_INTERVAL, default=current_poll): vol.All(
                vol.Coerce(int), vol.Range(min=5, max=300)
            ),
            vol.Optional(CONF_OCCUPANCY_TIMEOUT, default=current_timeout): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=600)
            ),
        }
    )


class DoorOccupancyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Door Occupancy."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Show the initial setup form and create the entry on submit."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        if user_input is not None:
            return self.async_create_entry(
                title="Door Occupancy",
                data={
                    CONF_POLL_INTERVAL: user_input.get(
                        CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                    ),
                    CONF_OCCUPANCY_TIMEOUT: user_input.get(
                        CONF_OCCUPANCY_TIMEOUT, DEFAULT_OCCUPANCY_TIMEOUT
                    ),
                },
            )

        return self.async_show_form(step_id="user", data_schema=_user_schema())

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return DoorOccupancyOptionsFlow()


class DoorOccupancyOptionsFlow(config_entries.OptionsFlow):
    """Options flow allowing both values to be reconfigured."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            new_data = dict(self.config_entry.data)
            new_data[CONF_POLL_INTERVAL] = user_input[CONF_POLL_INTERVAL]
            new_data[CONF_OCCUPANCY_TIMEOUT] = user_input[CONF_OCCUPANCY_TIMEOUT]
            self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=_user_schema(
                current_poll=self.config_entry.data.get(
                    CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
                ),
                current_timeout=self.config_entry.data.get(
                    CONF_OCCUPANCY_TIMEOUT, DEFAULT_OCCUPANCY_TIMEOUT
                ),
            ),
        )
