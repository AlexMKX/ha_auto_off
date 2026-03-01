"""Tests for auto_off services."""
import pytest
import yaml
from unittest.mock import MagicMock, AsyncMock

from custom_components.auto_off.const import (
    DOMAIN,
    CONF_GROUPS,
    CONF_GROUP_NAME,
    CONF_CONFIG,
)


class TestSetGroupService:
    """Test the set_group service (YAML-only contract)."""

    @pytest.mark.asyncio
    async def test_set_group_creates_new_group(
        self, hass, config_entry, sample_group_config_dict
    ):
        """Test set_group creates a new group from YAML config string."""
        from custom_components.auto_off import _async_register_services

        mock_manager = MagicMock()
        mock_manager.set_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)

        set_group_call = hass.services.async_register.call_args_list[0]
        handler = set_group_call[0][2]

        config_yaml = yaml.dump(sample_group_config_dict)
        call = MagicMock()
        call.data = {
            CONF_GROUP_NAME: "test_group",
            CONF_CONFIG: config_yaml,
        }

        await handler(call)

        mock_manager.set_group.assert_called_once()
        call_args = mock_manager.set_group.call_args
        assert call_args[0][0] == "test_group"
        assert call_args[0][1] == sample_group_config_dict
        assert call_args[0][2] is True  # is_new_group

    @pytest.mark.asyncio
    async def test_set_group_updates_existing_group(
        self, hass, config_entry, sample_group_config_dict
    ):
        """Test set_group updates an existing group."""
        from custom_components.auto_off import _async_register_services

        config_entry.data = {
            CONF_GROUPS: {"test_group": {"sensors": [], "targets": [], "delay": 0}},
            "poll_interval": 15,
        }

        mock_manager = MagicMock()
        mock_manager.set_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)

        set_group_call = hass.services.async_register.call_args_list[0]
        handler = set_group_call[0][2]

        config_yaml = yaml.dump(sample_group_config_dict)
        call = MagicMock()
        call.data = {
            CONF_GROUP_NAME: "test_group",
            CONF_CONFIG: config_yaml,
        }

        await handler(call)

        call_args = mock_manager.set_group.call_args
        assert call_args[0][2] is False  # is_new_group = False

    @pytest.mark.asyncio
    async def test_set_group_with_valid_yaml(self, hass, config_entry):
        """Test set_group accepts valid YAML config string."""
        from custom_components.auto_off import _async_register_services

        mock_manager = MagicMock()
        mock_manager.set_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)

        set_group_call = hass.services.async_register.call_args_list[0]
        handler = set_group_call[0][2]

        config_yaml = "sensors:\n  - binary_sensor.test\ntargets:\n  - light.test\ndelay: 5\n"
        call = MagicMock()
        call.data = {
            CONF_GROUP_NAME: "test_group",
            CONF_CONFIG: config_yaml,
        }

        await handler(call)

        mock_manager.set_group.assert_called_once()
        call_args = mock_manager.set_group.call_args
        config_dict = call_args[0][1]
        assert config_dict["sensors"] == ["binary_sensor.test"]
        assert config_dict["targets"] == ["light.test"]
        assert config_dict["delay"] == 5

    @pytest.mark.asyncio
    async def test_set_group_rejects_invalid_yaml(self, hass, config_entry):
        """Test set_group logs error on invalid YAML."""
        from custom_components.auto_off import _async_register_services

        mock_manager = MagicMock()
        mock_manager.set_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)

        set_group_call = hass.services.async_register.call_args_list[0]
        handler = set_group_call[0][2]

        call = MagicMock()
        call.data = {
            CONF_GROUP_NAME: "bad_group",
            CONF_CONFIG: "not valid yaml: [",
        }

        # Should not raise, just log error
        await handler(call)

        mock_manager.set_group.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_group_rejects_non_dict_yaml(self, hass, config_entry):
        """Test set_group rejects YAML that parses to non-dict."""
        from custom_components.auto_off import _async_register_services

        mock_manager = MagicMock()
        mock_manager.set_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)

        set_group_call = hass.services.async_register.call_args_list[0]
        handler = set_group_call[0][2]

        call = MagicMock()
        call.data = {
            CONF_GROUP_NAME: "bad_group",
            CONF_CONFIG: "- just a list",
        }

        await handler(call)

        mock_manager.set_group.assert_not_called()


class TestDeleteGroupService:
    """Test the delete_group service."""

    @pytest.mark.asyncio
    async def test_delete_group_removes_existing(self, hass, config_entry):
        """Test delete_group removes an existing group."""
        from custom_components.auto_off import _async_register_services

        config_entry.data = {
            CONF_GROUPS: {"test_group": {"sensors": [], "targets": [], "delay": 0}},
            "poll_interval": 15,
        }

        mock_manager = MagicMock()
        mock_manager.delete_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)

        delete_group_call = hass.services.async_register.call_args_list[1]
        handler = delete_group_call[0][2]

        call = MagicMock()
        call.data = {CONF_GROUP_NAME: "test_group"}

        await handler(call)

        mock_manager.delete_group.assert_called_once_with("test_group")

    @pytest.mark.asyncio
    async def test_delete_group_warns_on_nonexistent(self, hass, config_entry):
        """Test delete_group warns when group doesn't exist."""
        from custom_components.auto_off import _async_register_services

        mock_manager = MagicMock()
        mock_manager.delete_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)

        delete_group_call = hass.services.async_register.call_args_list[1]
        handler = delete_group_call[0][2]

        call = MagicMock()
        call.data = {CONF_GROUP_NAME: "nonexistent_group"}

        await handler(call)

        # Manager should not be called for nonexistent group
        mock_manager.delete_group.assert_not_called()
