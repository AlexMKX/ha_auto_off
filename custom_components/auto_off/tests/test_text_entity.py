"""Tests for auto_off delay text entity."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.auto_off.const import CONF_DELAY, DOMAIN
from custom_components.auto_off.text import DelayTextEntity


class TestDelayTextEntity:
    """Test DelayTextEntity class."""

    @pytest.fixture
    def mock_manager(self):
        """Create mock IntegrationManager."""
        manager = MagicMock()
        manager.update_group_config = AsyncMock()
        return manager

    @pytest.fixture
    def delay_entity(self, hass, mock_manager, sample_group_config_dict):
        """Create a DelayTextEntity instance."""
        return DelayTextEntity(hass, mock_manager, "test_group", sample_group_config_dict)

    def test_init_exposes_configured_delay_and_stable_unique_id(self, delay_entity, sample_group_config_dict):
        """On init the entity must expose the configured delay as its
        observable value (what the UI shows) and a stable unique_id that
        survives restarts (what the entity registry keys on)."""
        delay_minutes = sample_group_config_dict[CONF_DELAY]
        # `native_value` and `unique_id` are public HA entity contracts.
        assert delay_entity.native_value == str(delay_minutes)
        assert delay_entity.unique_id == f"{DOMAIN}_test_group_delay"

    def test_device_info(self, delay_entity):
        """Test device_info returns correct identifiers."""
        device_info = delay_entity.device_info
        assert device_info is not None
        assert (DOMAIN, "test_group") in device_info["identifiers"]

    @pytest.mark.asyncio
    async def test_async_set_value_int(self, delay_entity, mock_manager):
        """Test async_set_value with integer (minutes)."""
        await delay_entity.async_set_value("10")

        mock_manager.update_group_config.assert_called_once()
        call_args = mock_manager.update_group_config.call_args[0]
        assert call_args[0] == "test_group"
        assert call_args[1][CONF_DELAY] == 10

    @pytest.mark.asyncio
    async def test_async_set_value_template(self, delay_entity, mock_manager):
        """Test async_set_value with template string."""
        template = "{{ states('input_number.delay') | int }}"
        await delay_entity.async_set_value(template)

        mock_manager.update_group_config.assert_called_once()
        call_args = mock_manager.update_group_config.call_args[0]
        assert call_args[1][CONF_DELAY] == template

    def test_update_config_refreshes_native_value_and_writes_ha_state(self, delay_entity):
        """`update_config` must (a) make the new delay observable via
        `native_value` and (b) push the change to HA so the UI re-renders."""
        new_config = {CONF_DELAY: 60}
        with patch.object(delay_entity, "async_write_ha_state") as mock_write:
            delay_entity.update_config(new_config)
        assert delay_entity.native_value == "60"
        mock_write.assert_called_once()
