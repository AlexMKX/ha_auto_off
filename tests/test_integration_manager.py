"""Tests for auto_off integration manager."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import yaml

from custom_components.auto_off.integration_manager import (
    IntegrationManager,
    parse_group_configs,
    async_setup_integration,
    async_unload_integration,
)
from custom_components.auto_off.const import DOMAIN, CONF_GROUPS, CONF_POLL_INTERVAL


class TestParseGroupConfigs:
    """Test parse_group_configs function."""

    def test_parse_valid_config(self, sample_group_config_yaml):
        """Test parsing valid YAML config."""
        groups_data = {"test_group": sample_group_config_yaml}
        result = parse_group_configs(groups_data)

        assert "test_group" in result
        assert result["test_group"].sensors == ["binary_sensor.motion_living_room"]
        assert result["test_group"].targets == ["light.living_room"]
        assert result["test_group"].delay == 5

    def test_parse_invalid_yaml(self):
        """Test parsing invalid YAML returns empty."""
        groups_data = {"bad_group": "invalid: yaml: ["}
        result = parse_group_configs(groups_data)

        assert "bad_group" not in result

    def test_parse_empty_groups(self):
        """Test parsing empty groups dict."""
        result = parse_group_configs({})
        assert result == {}

    def test_parse_multiple_groups(self):
        """Test parsing multiple groups."""
        groups_data = {
            "group1": "sensors:\n  - sensor.a\ntargets:\n  - light.a\ndelay: 1",
            "group2": "sensors:\n  - sensor.b\ntargets:\n  - light.b\ndelay: 2",
        }
        result = parse_group_configs(groups_data)

        assert len(result) == 2
        assert "group1" in result
        assert "group2" in result


class TestIntegrationManager:
    """Test IntegrationManager class."""

    @pytest.fixture
    def manager(self, hass, config_entry, async_add_entities):
        """Create an IntegrationManager instance."""
        with patch(
            "custom_components.auto_off.integration_manager.AutoOffManager"
        ) as mock_aom:
            with patch(
                "custom_components.auto_off.integration_manager.DoorOccupancyManager"
            ) as mock_dom:
                mock_aom.return_value = MagicMock()
                mock_dom.return_value = MagicMock()
                manager = IntegrationManager(hass, config_entry, async_add_entities)
                manager.auto_off = MagicMock()
                manager.auto_off.config = {}
                manager.auto_off._groups = {}
                manager.auto_off._init_groups = MagicMock()
                manager.auto_off.async_unload = AsyncMock()
                manager.door_occupancy = MagicMock()
                manager.door_occupancy._discover_and_add_sensors = AsyncMock()
                manager.door_occupancy.periodic_discovery = AsyncMock()
                manager.door_occupancy.async_unload = AsyncMock()
                return manager

    @pytest.mark.asyncio
    async def test_set_group_creates_new(
        self, manager, sample_group_config_yaml
    ):
        """Test set_group creates a new group."""
        manager._text_async_add_entities = MagicMock()

        await manager.set_group("new_group", sample_group_config_yaml, is_new=True)

        assert "new_group" in manager._groups_yaml
        assert manager._groups_yaml["new_group"] == sample_group_config_yaml
        manager.auto_off._init_groups.assert_called_once()
        manager._text_async_add_entities.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_group_updates_existing(
        self, manager, sample_group_config_yaml
    ):
        """Test set_group updates an existing group."""
        manager._groups_yaml["existing_group"] = "old_config"
        mock_text_entity = MagicMock()
        manager._text_entities["existing_group"] = mock_text_entity

        await manager.set_group("existing_group", sample_group_config_yaml, is_new=False)

        assert manager._groups_yaml["existing_group"] == sample_group_config_yaml
        mock_text_entity.update_config.assert_called_once_with(sample_group_config_yaml)

    @pytest.mark.asyncio
    async def test_delete_group(self, manager):
        """Test delete_group removes a group."""
        manager._groups_yaml["to_delete"] = "some_config"
        manager.auto_off.config["to_delete"] = MagicMock()
        mock_group = MagicMock()
        mock_group.async_unload = AsyncMock()
        manager.auto_off._groups["to_delete"] = mock_group

        with patch(
            "custom_components.auto_off.integration_manager.er.async_get"
        ) as mock_er:
            with patch(
                "custom_components.auto_off.integration_manager.dr.async_get"
            ) as mock_dr:
                mock_er.return_value = MagicMock()
                mock_dr.return_value = MagicMock()
                mock_dr.return_value.async_get_device.return_value = None

                await manager.delete_group("to_delete")

        assert "to_delete" not in manager._groups_yaml
        assert "to_delete" not in manager.auto_off.config
        mock_group.async_unload.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_initialize(self, manager, hass):
        """Test async_initialize sets up periodic worker."""
        with patch(
            "custom_components.auto_off.integration_manager.async_track_time_interval"
        ) as mock_track:
            mock_track.return_value = MagicMock()
            await manager.async_initialize()

        mock_track.assert_called_once()
        manager.door_occupancy._discover_and_add_sensors.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_unload(self, manager):
        """Test async_unload cleans up resources."""
        mock_remove_listener = MagicMock()
        manager._remove_listener = mock_remove_listener

        await manager.async_unload()

        mock_remove_listener.assert_called_once()
        manager.auto_off.async_unload.assert_called_once()
        manager.door_occupancy.async_unload.assert_called_once()

    def test_text_platform_ready(self, manager, sample_group_config_yaml):
        """Test text_platform_ready creates entities for existing groups."""
        manager._groups_yaml = {"group1": sample_group_config_yaml}
        mock_add_entities = MagicMock()

        with patch(
            "custom_components.auto_off.text.GroupConfigTextEntity"
        ) as mock_entity_class:
            mock_entity_class.return_value = MagicMock()
            manager.text_platform_ready(mock_add_entities)

        mock_add_entities.assert_called_once()
        assert len(mock_add_entities.call_args[0][0]) == 1


class TestAsyncSetupIntegration:
    """Test async_setup_integration function."""

    @pytest.mark.asyncio
    async def test_setup_creates_manager(self, hass, config_entry, async_add_entities):
        """Test setup creates and initializes manager."""
        with patch(
            "custom_components.auto_off.integration_manager.IntegrationManager"
        ) as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager.async_initialize = AsyncMock()
            mock_manager_class.return_value = mock_manager

            result = await async_setup_integration(hass, config_entry, async_add_entities)

        assert result is True
        assert hass.data[DOMAIN] == mock_manager
        mock_manager.async_initialize.assert_called_once()


class TestAsyncUnloadIntegration:
    """Test async_unload_integration function."""

    @pytest.mark.asyncio
    async def test_unload_cleans_up(self, hass, config_entry):
        """Test unload cleans up manager."""
        mock_manager = MagicMock()
        mock_manager.async_unload = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        result = await async_unload_integration(hass, config_entry)

        assert result is True
        assert DOMAIN not in hass.data
        mock_manager.async_unload.assert_called_once()
