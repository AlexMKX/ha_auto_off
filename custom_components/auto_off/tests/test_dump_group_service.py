"""Tests for the ``auto_off.dump_group`` service.

The service takes a group_name and returns a ServiceResponse containing a
YAML block that, when pasted into Developer Tools → Services, recreates
the same group via ``auto_off.set_group``. The YAML always includes
every configurable field (even when equal to the default), so a round
trip is exact and the operator can edit the block without first
inferring which fields the group does or does not have.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ServiceValidationError

from custom_components.auto_off import (
    SERVICE_DUMP_GROUP_SCHEMA,
    _async_register_services,
)


@pytest.fixture
def entry_with_groups():
    """ConfigEntry stub with two groups: a minimal one and a fully spelled out one."""
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "auto-off-entry"
    entry.data = {
        "groups": {
            "kitchen_minimal": {
                "targets": ["light.kitchen"],
                "sensors": ["binary_sensor.kitchen_motion"],
                "sensor_templates": [],
                "delay": 5,
            },
            "office_full": {
                "targets": ["light.office_a", "switch.office_fan"],
                "sensors": ["binary_sensor.office_motion"],
                "sensor_templates": ["{{ is_state('schedule.work', 'on') }}"],
                "delay": 15,
                "ensure_window": 90,
                "ensure_interval": 15,
            },
        }
    }
    entry.options = {}
    return entry


@pytest.fixture
async def hass_with_service(hass, entry_with_groups):
    """Register the auto_off services against a stub hass and stub manager."""
    # Real registry-like service store on the mock hass.
    services_registered: dict[str, MagicMock] = {}

    def _async_register(domain, name, handler, schema=None, supports_response=None):
        services_registered[f"{domain}.{name}"] = MagicMock(
            handler=handler, schema=schema, supports_response=supports_response
        )

    hass.services.async_register = MagicMock(side_effect=_async_register)
    hass.services._registered = services_registered

    # Manager stub – dump_group does not need to call it; set_group does.
    manager = MagicMock()
    manager.set_group = AsyncMock()
    manager.delete_group = AsyncMock()
    hass.data = {"auto_off": manager}

    await _async_register_services(hass, entry_with_groups)
    return hass


class TestDumpGroupRegistration:
    """The service must be registered with response-only support and a
    schema that accepts a single ``group_name`` field."""

    async def test_registered_with_supports_response_only(self, hass_with_service):
        entry = hass_with_service.services._registered.get("auto_off.dump_group")
        assert entry is not None, "auto_off.dump_group was not registered"
        # Caller MUST request response data; this is read-only.
        from homeassistant.core import SupportsResponse

        assert entry.supports_response == SupportsResponse.ONLY

    def test_schema_requires_group_name_only(self):
        # group_name is required, everything else is rejected.
        valid = SERVICE_DUMP_GROUP_SCHEMA({"group_name": "kitchen"})
        assert valid["group_name"] == "kitchen"

        with pytest.raises(Exception):
            SERVICE_DUMP_GROUP_SCHEMA({})  # missing required


class TestDumpGroupBehavior:
    """Behavior of the registered service callback."""

    async def _call(self, hass_with_service, group_name):
        """Invoke the dump_group handler with a minimal ServiceCall stub."""
        entry = hass_with_service.services._registered["auto_off.dump_group"]
        call = MagicMock()
        call.data = {"group_name": group_name}
        call.return_response = True
        return await entry.handler(call)

    async def test_unknown_group_raises_service_validation_error(
        self, hass_with_service
    ):
        with pytest.raises(ServiceValidationError):
            await self._call(hass_with_service, "no_such_group")

    async def test_response_is_native_action_dict(self, hass_with_service):
        """The response must be a native dict with ``action`` and ``data``
        keys at the top level, not a string-wrapped YAML block.

        HA serializes ServiceResponse dicts directly to YAML in the UI;
        returning a native dict avoids the ``yaml: |`` literal-block
        wrapper that appears when the value is a multi-line string. The
        operator can copy the response straight into Developer Tools →
        Actions without unwrapping anything.

        Uses the modern ``action:`` key (Home Assistant 2024.8+ renamed
        ``service:`` to ``action:`` in scripts/automations YAML; the old
        key still works but the UI now writes ``action:``).
        """
        from custom_components.auto_off import SERVICE_SET_GROUP_SCHEMA

        response = await self._call(hass_with_service, "kitchen_minimal")

        # Top-level shape: action + data, nothing else.
        assert response["action"] == "auto_off.set_group"
        assert isinstance(response["data"], dict)
        assert "yaml" not in response
        assert "service" not in response

        # data Round-trips through the set_group schema with no edits.
        validated = SERVICE_SET_GROUP_SCHEMA(response["data"])
        assert validated["group_name"] == "kitchen_minimal"
        assert validated["targets"] == ["light.kitchen"]
        assert validated["sensors"] == ["binary_sensor.kitchen_motion"]
        assert validated["sensor_templates"] == []
        assert validated["delay"] == 5

    async def test_data_includes_every_field_even_when_default(
        self, hass_with_service
    ):
        """Even fields equal to their defaults are emitted, so the user can
        edit them without first remembering the defaults."""
        response = await self._call(hass_with_service, "kitchen_minimal")
        data = response["data"]

        # Required field
        assert "group_name" in data
        # All configurable GroupConfig fields, defaulted or not.
        assert "targets" in data
        assert "sensors" in data
        assert "sensor_templates" in data
        assert "delay" in data
        assert "ensure_window" in data
        assert "ensure_interval" in data

    async def test_data_preserves_non_default_ensure_settings(
        self, hass_with_service
    ):
        """When the stored group overrides ensure_window / ensure_interval,
        the dump must echo those values rather than the GroupConfig defaults."""
        response = await self._call(hass_with_service, "office_full")
        data = response["data"]

        assert data["group_name"] == "office_full"
        assert data["delay"] == 15
        assert data["ensure_window"] == 90
        assert data["ensure_interval"] == 15
        assert data["sensor_templates"] == [
            "{{ is_state('schedule.work', 'on') }}"
        ]
