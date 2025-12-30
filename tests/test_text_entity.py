"""Tests for auto_off text entity."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from custom_components.auto_off.text import GroupConfigTextEntity, _config_to_display
from custom_components.auto_off.const import DOMAIN, CONF_SENSORS, CONF_TARGETS, CONF_DELAY


class TestGroupConfigTextEntity:
    """Test GroupConfigTextEntity class."""

    @pytest.fixture
    def text_entity(self, hass, config_entry, sample_group_config_dict):
        """Create a GroupConfigTextEntity instance."""
        return GroupConfigTextEntity(
            hass, config_entry, "test_group", sample_group_config_dict
        )

    def test_init(self, text_entity, sample_group_config_dict):
        """Test entity initialization."""
        assert text_entity._group_name == "test_group"
        assert text_entity._config_dict == sample_group_config_dict
        expected_display = _config_to_display(sample_group_config_dict)
        assert text_entity._attr_native_value == expected_display
        assert text_entity._attr_unique_id == f"{DOMAIN}_test_group_config"

    def test_device_info(self, text_entity):
        """Test device_info returns correct identifiers."""
        device_info = text_entity.device_info

        assert device_info is not None
        assert (DOMAIN, "test_group") in device_info["identifiers"]
        assert "Auto Off: test_group" in device_info["name"]

    @pytest.mark.asyncio
    async def test_async_set_value_logs_warning(self, text_entity, hass):
        """Test async_set_value logs warning (direct edit not supported)."""
        original_value = text_entity._attr_native_value
        
        await text_entity.async_set_value("any value")
        
        # Value should not change since direct edit is not supported
        assert text_entity._attr_native_value == original_value

    def test_update_config(self, text_entity):
        """Test update_config updates the value."""
        new_config = {
            CONF_SENSORS: ["binary_sensor.new"],
            CONF_TARGETS: ["light.new"],
            CONF_DELAY: 10,
        }

        with patch.object(text_entity, 'async_write_ha_state'):
            text_entity.update_config(new_config)

        assert text_entity._config_dict == new_config
        assert text_entity._attr_native_value == _config_to_display(new_config)

    def test_extra_state_attributes(self, text_entity, sample_group_config_dict):
        """Test extra_state_attributes returns config data."""
        attrs = text_entity.extra_state_attributes
        
        assert attrs[CONF_SENSORS] == sample_group_config_dict[CONF_SENSORS]
        assert attrs[CONF_TARGETS] == sample_group_config_dict[CONF_TARGETS]
        assert attrs[CONF_DELAY] == sample_group_config_dict[CONF_DELAY]

    def test_entity_attributes(self, text_entity):
        """Test entity has correct attributes."""
        assert text_entity._attr_has_entity_name is True
        assert text_entity._attr_name == "Config"
        assert text_entity._attr_native_max == 10000
