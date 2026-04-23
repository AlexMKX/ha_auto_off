"""Tests for the door_occupancy config flow."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.door_occupancy.config_flow import (
    DoorOccupancyConfigFlow,
    DoorOccupancyOptionsFlow,
)
from custom_components.door_occupancy.const import (
    CONF_OCCUPANCY_TIMEOUT,
    CONF_POLL_INTERVAL,
    DEFAULT_OCCUPANCY_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
)


class TestDoorOccupancyConfigFlow:
    """Initial setup config flow."""

    @pytest.fixture
    def flow(self):
        flow = DoorOccupancyConfigFlow()
        flow.hass = MagicMock()
        flow.hass.config_entries = MagicMock()
        return flow

    @pytest.mark.asyncio
    async def test_step_user_shows_form_when_no_input(self, flow):
        with patch.object(flow, "async_set_unique_id", new_callable=AsyncMock):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                result = await flow.async_step_user(user_input=None)

        assert result["type"] == "form"
        assert result["step_id"] == "user"
        assert CONF_POLL_INTERVAL in result["data_schema"].schema
        assert CONF_OCCUPANCY_TIMEOUT in result["data_schema"].schema

    @pytest.mark.asyncio
    async def test_step_user_creates_entry_with_defaults(self, flow):
        with patch.object(flow, "async_set_unique_id", new_callable=AsyncMock):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                with patch.object(flow, "async_create_entry") as mock_create:
                    mock_create.return_value = {"type": "create_entry"}
                    await flow.async_step_user(user_input={})

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["title"] == "Door Occupancy"
        assert call_kwargs["data"][CONF_POLL_INTERVAL] == DEFAULT_POLL_INTERVAL
        assert call_kwargs["data"][CONF_OCCUPANCY_TIMEOUT] == DEFAULT_OCCUPANCY_TIMEOUT

    @pytest.mark.asyncio
    async def test_step_user_creates_entry_with_user_values(self, flow):
        with patch.object(flow, "async_set_unique_id", new_callable=AsyncMock):
            with patch.object(flow, "_abort_if_unique_id_configured"):
                with patch.object(flow, "async_create_entry") as mock_create:
                    mock_create.return_value = {"type": "create_entry"}
                    await flow.async_step_user(
                        user_input={
                            CONF_POLL_INTERVAL: 60,
                            CONF_OCCUPANCY_TIMEOUT: 30,
                        }
                    )

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["data"][CONF_POLL_INTERVAL] == 60
        assert call_kwargs["data"][CONF_OCCUPANCY_TIMEOUT] == 30


class TestDoorOccupancyOptionsFlow:
    """Options flow for reconfiguration."""

    @pytest.mark.skip(reason="Requires full HA test harness with frame helper")
    @pytest.mark.asyncio
    async def test_init_step_updates_entry(self):
        """Covered by e2e tests; mirrors AutoOffOptionsFlow pattern."""
        pass
