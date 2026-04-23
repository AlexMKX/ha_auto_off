"""Tests for DeadlineSensorEntity attribute exposure."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.auto_off.auto_off import GroupConfig
from custom_components.auto_off.sensor import DeadlineSensorEntity


def _make_entity(manager, group_name="kitchen"):
    hass = MagicMock()
    entry = MagicMock()
    return DeadlineSensorEntity(hass, entry, group_name, manager)


def test_entity_holds_manager_reference():
    manager = MagicMock()
    entity = _make_entity(manager)
    assert entity._manager is manager


def test_attributes_include_group_fields_when_config_available():
    manager = MagicMock()
    manager.get_group_config.return_value = GroupConfig(
        targets=["light.a", "switch.b"],
        sensors=["binary_sensor.motion"],
        sensor_templates=["{{ 1 }}"],
        delay=5,
    )
    entity = _make_entity(manager)
    attrs = entity.extra_state_attributes
    assert attrs["targets"] == ["light.a", "switch.b"]
    assert attrs["sensors"] == ["binary_sensor.motion"]
    assert attrs["sensor_templates"] == ["{{ 1 }}"]
    assert "deadline_iso" in attrs


def test_attributes_fall_back_when_group_missing():
    manager = MagicMock()
    manager.get_group_config.return_value = None
    entity = _make_entity(manager)
    attrs = entity.extra_state_attributes
    assert attrs == {"deadline_iso": None}
