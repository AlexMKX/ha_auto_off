"""Tests for auto_off text entity."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from custom_components.auto_off.text import GroupConfigTextEntity
from custom_components.auto_off.const import DOMAIN


class TestGroupConfigTextEntity:
    """Test GroupConfigTextEntity class."""

    @pytest.fixture
    def text_entity(self, hass, config_entry, sample_group_config_yaml):
        """Create a GroupConfigTextEntity instance."""
        return GroupConfigTextEntity(
            hass, config_entry, "test_group", sample_group_config_yaml
        )

    def test_init(self, text_entity, sample_group_config_yaml):
        """Test entity initialization."""
        assert text_entity._group_name == "test_group"
        assert text_entity._attr_native_value == sample_group_config_yaml
        assert text_entity._attr_unique_id == f"{DOMAIN}_test_group_config"

    def test_device_info(self, text_entity):
        """Test device_info returns correct identifiers."""
        device_info = text_entity.device_info

        assert device_info is not None
        assert (DOMAIN, "test_group") in device_info["identifiers"]
        assert "Auto Off: test_group" in device_info["name"]

    @pytest.mark.asyncio
    async def test_async_set_value_valid_yaml(
        self, text_entity, hass, sample_group_config_yaml
    ):
        """Test async_set_value with valid YAML."""
        mock_manager = MagicMock()
        mock_manager.update_group_config = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        new_config = """sensors:
  - binary_sensor.new_sensor
targets:
  - light.new_light
delay: 10
"""
        await text_entity.async_set_value(new_config)

        mock_manager.update_group_config.assert_called_once_with("test_group", new_config)
        assert text_entity._attr_native_value == new_config

    @pytest.mark.asyncio
    async def test_async_set_value_invalid_yaml(self, text_entity, hass):
        """Test async_set_value with invalid YAML does not update."""
        mock_manager = MagicMock()
        mock_manager.update_group_config = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        original_value = text_entity._attr_native_value

        await text_entity.async_set_value("invalid: yaml: [")

        mock_manager.update_group_config.assert_not_called()
        assert text_entity._attr_native_value == original_value

    @pytest.mark.asyncio
    async def test_async_set_value_non_dict_yaml(self, text_entity, hass):
        """Test async_set_value with non-dict YAML does not update."""
        mock_manager = MagicMock()
        mock_manager.update_group_config = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        original_value = text_entity._attr_native_value

        await text_entity.async_set_value("- item1\n- item2")  # List, not dict

        mock_manager.update_group_config.assert_not_called()
        assert text_entity._attr_native_value == original_value

    def test_update_config(self, text_entity):
        """Test update_config updates the value."""
        new_config = "new: config"

        with patch.object(text_entity, 'async_write_ha_state'):
            text_entity.update_config(new_config)

        assert text_entity._attr_native_value == new_config

    def test_entity_attributes(self, text_entity):
        """Test entity has correct attributes."""
        assert text_entity._attr_has_entity_name is True
        assert text_entity._attr_name == "Config"
        assert text_entity._attr_native_max == 10000
