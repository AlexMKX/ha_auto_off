"""Tests for auto_off delay text entity."""
import pytest
from unittest.mock import MagicMock, AsyncMock

from custom_components.auto_off.text import DelayTextEntity
from custom_components.auto_off.const import DOMAIN, CONF_DELAY


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
        return DelayTextEntity(
            hass, mock_manager, "test_group", sample_group_config_dict
        )

    def test_init(self, delay_entity, sample_group_config_dict):
        """Test entity initialization - delay is in minutes."""
        delay_minutes = sample_group_config_dict[CONF_DELAY]
        assert delay_entity._attr_native_value == str(delay_minutes)
        assert delay_entity._attr_name == "Delay (minutes)"
        assert delay_entity._attr_unique_id == f"{DOMAIN}_test_group_delay"

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

    def test_update_config(self, delay_entity):
        """Test update_config updates the value."""
        new_config = {CONF_DELAY: 60}
        delay_entity.update_config(new_config)
        assert delay_entity._attr_native_value == "60"
