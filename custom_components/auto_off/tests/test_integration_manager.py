"""Tests for auto_off integration manager."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.auto_off.auto_off import GroupConfig
from custom_components.auto_off.integration_manager import (
    IntegrationManager,
    parse_group_configs,
)


class TestParseGroupConfigs:
    """Test parse_group_configs function."""

    def test_parse_valid_config(self, sample_group_config_dict):
        """Test parsing valid dict config."""
        groups_data = {"test_group": sample_group_config_dict}
        result = parse_group_configs(groups_data)

        assert "test_group" in result
        assert result["test_group"].sensors == ["binary_sensor.motion_living_room"]
        assert result["test_group"].targets == ["light.living_room"]
        assert result["test_group"].delay == 5

    def test_parse_invalid_config(self):
        """Test parsing invalid config returns empty."""
        groups_data = {"bad_group": "not a dict"}
        result = parse_group_configs(groups_data)

        assert "bad_group" not in result

    def test_parse_empty_groups(self):
        """Test parsing empty groups dict."""
        result = parse_group_configs({})
        assert result == {}

    def test_parse_multiple_groups(self):
        """Test parsing multiple groups."""
        groups_data = {
            "group1": {"sensors": ["sensor.a"], "targets": ["light.a"], "delay": 1},
            "group2": {"sensors": ["sensor.b"], "targets": ["light.b"], "delay": 2},
        }
        result = parse_group_configs(groups_data)

        assert len(result) == 2
        assert "group1" in result
        assert "group2" in result


class TestIntegrationManager:
    """Test IntegrationManager class."""

    @pytest.fixture
    def manager(self, hass, config_entry):
        """Create an IntegrationManager instance."""
        with patch("custom_components.auto_off.integration_manager.AutoOffManager") as mock_aom:
            mock_aom.return_value = MagicMock()
            manager = IntegrationManager(hass, config_entry)
            manager.auto_off = MagicMock()
            manager.auto_off.config = {}
            manager.auto_off._groups = {}
            manager.auto_off.async_init_groups = AsyncMock()
            manager.auto_off.async_unload = AsyncMock()
            return manager

    @pytest.mark.asyncio
    async def test_set_group_creates_new(self, manager, sample_group_config_dict):
        manager._text_async_add_entities = MagicMock()
        manager._sensor_async_add_entities = MagicMock()

        await manager.set_group("new_group", sample_group_config_dict, is_new=True)

        assert "new_group" in manager._groups_data
        assert manager._groups_data["new_group"] == sample_group_config_dict
        manager.auto_off.async_init_groups.assert_awaited_once()
        manager._text_async_add_entities.assert_called_once()
        # Exactly one deadline sensor is created, no config sensor.
        manager._sensor_async_add_entities.assert_called_once()
        added = manager._sensor_async_add_entities.call_args[0][0]
        assert len(added) == 1

    @pytest.mark.asyncio
    async def test_set_group_updates_existing(self, manager, sample_group_config_dict):
        """Test set_group updates an existing group."""
        manager._groups_data["existing_group"] = {"sensors": [], "targets": [], "delay": 0}
        mock_text_entity = MagicMock()
        manager._text_entities["existing_group"] = mock_text_entity

        await manager.set_group("existing_group", sample_group_config_dict, is_new=False)

        assert manager._groups_data["existing_group"] == sample_group_config_dict
        mock_text_entity.update_config.assert_called_once_with(sample_group_config_dict)

    @pytest.mark.asyncio
    async def test_delete_group(self, manager):
        """Test delete_group removes a group."""
        manager._groups_data["to_delete"] = {"sensors": [], "targets": [], "delay": 0}
        manager.auto_off.config["to_delete"] = MagicMock()
        mock_group = MagicMock()
        mock_group.async_unload = AsyncMock()
        manager.auto_off._groups["to_delete"] = mock_group

        with (
            patch("custom_components.auto_off.integration_manager.er.async_get") as mock_er,
            patch("custom_components.auto_off.integration_manager.dr.async_get") as mock_dr,
        ):
            mock_er.return_value = MagicMock()
            mock_dr.return_value = MagicMock()
            mock_dr.return_value.async_get_device.return_value = None

            await manager.delete_group("to_delete")

        assert "to_delete" not in manager._groups_data
        assert "to_delete" not in manager.auto_off.config
        mock_group.async_unload.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_initialize(self, manager, hass):
        """Test async_initialize sets up periodic worker."""
        with patch("custom_components.auto_off.integration_manager.async_track_time_interval") as mock_track:
            mock_track.return_value = MagicMock()
            await manager.async_initialize()

        mock_track.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_unload(self, manager):
        """Test async_unload cleans up resources."""
        mock_remove_listener = MagicMock()
        manager._remove_listener = mock_remove_listener

        await manager.async_unload()

        mock_remove_listener.assert_called_once()
        manager.auto_off.async_unload.assert_called_once()

    def test_text_platform_ready(self, manager, sample_group_config_dict):
        """Test text_platform_ready creates entities for existing groups."""
        manager._groups_data = {"group1": sample_group_config_dict}
        mock_add_entities = MagicMock()

        with patch("custom_components.auto_off.text.DelayTextEntity") as mock_entity_class:
            mock_entity_class.return_value = MagicMock()
            manager.text_platform_ready(mock_add_entities)

        mock_add_entities.assert_called_once()
        assert len(mock_add_entities.call_args[0][0]) == 1


class TestGetGroupConfig:
    """`get_group_config` is observed by deadline sensor attributes; it must
    round-trip whatever was installed through the public `set_group` API."""

    async def test_round_trips_config_installed_via_set_group(self, hass, config_entry):
        config_entry.data = {"poll_interval": 15, "groups": {}}
        mgr = IntegrationManager(hass, config_entry)

        installed_config = {
            "targets": ["light.kitchen"],
            "sensors": ["binary_sensor.motion"],
            "delay": 5,
        }

        # `set_group` delegates to AutoOffManager.async_init_groups to build
        # the actual SensorGroup. In a unit test we simulate that step: when
        # init runs, pretend a SensorGroup was created with this config.
        async def fake_init_groups():
            group = MagicMock()
            group._config = GroupConfig.model_validate(installed_config)
            group.check_and_set_deadline = AsyncMock()
            group._get_human_deadline = MagicMock(return_value="None")
            mgr.auto_off._groups["kitchen"] = group

        mgr.auto_off.async_init_groups = AsyncMock(side_effect=fake_init_groups)

        # Stub platform entity factories so set_group doesn't touch real HA
        # entity plumbing. This keeps the focus of the test on the
        # set_group -> get_group_config contract.
        with (
            patch("custom_components.auto_off.sensor.DeadlineSensorEntity") as mock_sensor_cls,
            patch("custom_components.auto_off.text.DelayTextEntity") as mock_text_cls,
        ):
            mock_sensor_cls.return_value = MagicMock()
            mock_text_cls.return_value = MagicMock()
            mgr._sensor_async_add_entities = MagicMock()
            mgr._text_async_add_entities = MagicMock()

            await mgr.set_group("kitchen", installed_config, is_new=True)

        got = mgr.get_group_config("kitchen")
        assert got is not None
        assert got.targets == ["light.kitchen"]
        assert got.sensors == ["binary_sensor.motion"]
        assert got.delay == 5

    def test_returns_none_for_unknown_group(self, hass, config_entry):
        mgr = IntegrationManager(hass, config_entry)
        assert mgr.get_group_config("missing") is None


class TestUpdateGroupConfigWritesState:
    async def test_async_write_ha_state_called_after_update(self, hass, config_entry):
        config_entry.data = {
            "poll_interval": 15,
            "groups": {
                "kitchen": {
                    "targets": ["light.kitchen"],
                    "sensors": ["binary_sensor.motion"],
                    "delay": 5,
                }
            },
        }
        mgr = IntegrationManager(hass, config_entry)

        mock_entity = MagicMock()
        mock_entity.async_write_ha_state = MagicMock()
        mgr._deadline_entities["kitchen"] = mock_entity

        # Stub out auto_off.async_init_groups so set_group doesn't actually
        # try to build SensorGroup with a real hass.
        mgr.auto_off.async_init_groups = AsyncMock()

        await mgr.update_group_config(
            "kitchen",
            {
                "targets": ["light.kitchen", "light.extra"],
                "sensors": ["binary_sensor.motion"],
                "delay": 5,
            },
        )

        mock_entity.async_write_ha_state.assert_called()


class TestGroupEntityHelpers:
    async def test_get_group_config_returns_parsed(self, hass, config_entry):
        """get_group_config materialises stored dict as GroupConfig."""
        config_entry.data = {
            "poll_interval": 15,
            "groups": {
                "k": {
                    "targets": ["light.a", "switch.b"],
                    "sensors": ["binary_sensor.m"],
                    "sensor_templates": [],
                    "delay": 5,
                }
            },
        }
        manager = IntegrationManager(hass, config_entry)
        cfg = manager.get_group_config("k")
        assert cfg is not None
        assert cfg.targets == ["light.a", "switch.b"]
        assert cfg.sensors == ["binary_sensor.m"]

    async def test_get_group_config_unknown(self, hass, config_entry):
        config_entry.data = {"poll_interval": 15, "groups": {}}
        manager = IntegrationManager(hass, config_entry)
        assert manager.get_group_config("nope") is None

    async def test_get_group_targets_by_domain_buckets(self, hass, config_entry):
        config_entry.data = {
            "poll_interval": 15,
            "groups": {
                "k": {
                    "targets": [
                        "light.a",
                        "switch.b",
                        "light.c",
                        "scene.evening",  # non-groupable, dropped
                    ],
                    "sensors": ["binary_sensor.m"],
                    "sensor_templates": [],
                    "delay": 0,
                }
            },
        }
        manager = IntegrationManager(hass, config_entry)
        result = manager.get_group_targets_by_domain("k")
        assert result == {
            "light": ["light.a", "light.c"],
            "switch": ["switch.b"],
        }
