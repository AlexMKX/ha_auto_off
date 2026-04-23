"""Tests for DeadlineSensorEntity attribute exposure."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.auto_off.sensor import DeadlineSensorEntity


def _make_entity(group_name="kitchen"):
    hass = MagicMock()
    entry = MagicMock()
    manager = MagicMock()  # still accepted by __init__ but no longer stored
    return DeadlineSensorEntity(hass, entry, group_name, manager)


def test_attributes_only_contain_deadline_iso():
    """After Task 10 refactor, only deadline_iso is exposed."""
    entity = _make_entity()
    attrs = entity.extra_state_attributes
    assert set(attrs.keys()) == {"deadline_iso"}
    assert attrs["deadline_iso"] is None


def test_attributes_deadline_iso_updates():
    """deadline_iso reflects the last value passed to update_deadline."""
    entity = _make_entity()
    # Patch async_write_ha_state so we don't need a real entity platform
    entity.async_write_ha_state = MagicMock()
    entity.update_deadline("2026-04-23T12:00:00")
    attrs = entity.extra_state_attributes
    assert attrs["deadline_iso"] == "2026-04-23T12:00:00"
