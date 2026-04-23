"""Tests for the new structured auto_off.set_group service.

Covers behavior: validates via GroupConfig, constructs the internal
config dict in the shape the manager expects, and rejects invalid
combinations (empty targets, no sensor sources).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.auto_off.const import CONF_GROUPS, DOMAIN


class TestSetGroupStructured:
    async def test_creates_group_from_structured_fields(self, hass, config_entry, sample_set_group_payload):
        from custom_components.auto_off import _async_register_services

        mock_manager = MagicMock()
        mock_manager.set_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)
        handler = hass.services.async_register.call_args_list[0][0][2]

        call = MagicMock()
        call.data = sample_set_group_payload
        await handler(call)

        mock_manager.set_group.assert_called_once()
        args = mock_manager.set_group.call_args[0]
        assert args[0] == "kitchen"
        assert args[1] == {
            "targets": ["light.kitchen"],
            "sensors": ["binary_sensor.motion_kitchen"],
            "sensor_templates": [],
            "delay": 5,
        }
        assert args[2] is True  # is_new_group

    async def test_rejects_empty_targets(self, hass, config_entry):
        from custom_components.auto_off import _async_register_services

        mock_manager = MagicMock()
        mock_manager.set_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)
        handler = hass.services.async_register.call_args_list[0][0][2]

        call = MagicMock()
        call.data = {
            "group_name": "bad",
            "targets": [],
            "sensors": ["binary_sensor.a"],
        }
        await handler(call)

        mock_manager.set_group.assert_not_called()

    async def test_rejects_no_sensor_source(self, hass, config_entry):
        from custom_components.auto_off import _async_register_services

        mock_manager = MagicMock()
        mock_manager.set_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)
        handler = hass.services.async_register.call_args_list[0][0][2]

        call = MagicMock()
        call.data = {
            "group_name": "bad",
            "targets": ["light.a"],
            "sensors": [],
            "sensor_templates": [],
        }
        await handler(call)

        mock_manager.set_group.assert_not_called()

    async def test_accepts_sensor_templates_only(self, hass, config_entry):
        from custom_components.auto_off import _async_register_services

        mock_manager = MagicMock()
        mock_manager.set_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)
        handler = hass.services.async_register.call_args_list[0][0][2]

        call = MagicMock()
        call.data = {
            "group_name": "tpl_only",
            "targets": ["light.a"],
            "sensors": [],
            "sensor_templates": ["{{ is_state('light.a', 'on') }}"],
            "delay": 1,
        }
        await handler(call)

        mock_manager.set_group.assert_called_once()

    async def test_accepts_delay_as_template_string(self, hass, config_entry):
        from custom_components.auto_off import _async_register_services

        mock_manager = MagicMock()
        mock_manager.set_group = AsyncMock()
        hass.data[DOMAIN] = mock_manager

        await _async_register_services(hass, config_entry)
        handler = hass.services.async_register.call_args_list[0][0][2]

        template = "{{ states('input_number.delay') | int }}"
        call = MagicMock()
        call.data = {
            "group_name": "kitchen",
            "targets": ["light.a"],
            "sensors": ["binary_sensor.b"],
            "delay": template,
        }
        await handler(call)

        mock_manager.set_group.assert_called_once()
        args = mock_manager.set_group.call_args[0]
        assert args[1]["delay"] == template
