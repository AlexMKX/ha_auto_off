"""Tests for DoorOccupancyBinarySensor.

Covers: construction, state subscription, pulse on real state changes,
regression for the dropped-first-event bug, and invalid-state filtering.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from custom_components.door_occupancy.binary_sensor import (
    DoorOccupancyBinarySensor,
)


def _make_state(value: str):
    state = MagicMock()
    state.state = value
    return state


class TestDoorOccupancyBinarySensor:
    @pytest.fixture
    def source_state_closed(self):
        return _make_state("off")  # for binary_sensor door: off = closed

    @pytest.fixture
    def config_entry(self):
        entry = MagicMock()
        entry.entry_id = "test_entry"
        return entry

    @pytest.fixture
    def sensor(self, hass, config_entry, source_state_closed):
        hass.states.get.return_value = source_state_closed
        return DoorOccupancyBinarySensor(
            hass=hass,
            source_entity_id="binary_sensor.front_door",
            config_entry=config_entry,
            occupancy_timeout=15,
        )

    def test_init_sets_expected_attributes(self, sensor):
        assert sensor._attr_name == "binary_sensor.front_door Occupancy"
        assert sensor._attr_unique_id == "binary_sensor_front_door_occupancy"
        assert sensor._attr_is_on is False
        assert sensor._attr_should_poll is False

    async def test_added_to_hass_subscribes_and_reads_initial_state(self, hass, sensor, source_state_closed):
        with (
            patch("custom_components.door_occupancy.binary_sensor.async_track_state_change_event") as mock_track,
            patch("custom_components.door_occupancy.binary_sensor.entity_registry"),
            patch("custom_components.door_occupancy.binary_sensor.device_registry"),
        ):
            mock_track.return_value = lambda: None
            await sensor.async_added_to_hass()

        mock_track.assert_called_once()
        args, _ = mock_track.call_args
        assert args[1] == ["binary_sensor.front_door"]
        # Initial state was captured, not erroneously treated as a change.
        assert sensor._prev_state == "off"
        assert sensor._attr_is_on is False

    async def test_first_state_change_triggers_pulse(self, hass, sensor):
        """Regression: the first real state change must trigger pulse().

        The old implementation required both old_state and new_state to be
        non-None and silently dropped the initial change.
        """
        with (
            patch(
                "custom_components.door_occupancy.binary_sensor.async_track_state_change_event",
                return_value=lambda: None,
            ),
            patch("custom_components.door_occupancy.binary_sensor.entity_registry"),
            patch("custom_components.door_occupancy.binary_sensor.device_registry"),
        ):
            await sensor.async_added_to_hass()

        event = MagicMock()
        event.data = {
            "entity_id": "binary_sensor.front_door",
            "new_state": _make_state("on"),
        }
        with patch.object(sensor, "pulse") as mock_pulse:
            sensor._handle_source_event(event)

        mock_pulse.assert_called_once()
        assert sensor._prev_state == "on"

    async def test_unavailable_state_is_ignored(self, hass, sensor):
        with (
            patch(
                "custom_components.door_occupancy.binary_sensor.async_track_state_change_event",
                return_value=lambda: None,
            ),
            patch("custom_components.door_occupancy.binary_sensor.entity_registry"),
            patch("custom_components.door_occupancy.binary_sensor.device_registry"),
        ):
            await sensor.async_added_to_hass()

        event = MagicMock()
        event.data = {
            "entity_id": "binary_sensor.front_door",
            "new_state": _make_state("unavailable"),
        }
        with patch.object(sensor, "pulse") as mock_pulse:
            sensor._handle_source_event(event)

        mock_pulse.assert_not_called()

    async def test_same_state_is_not_a_pulse(self, hass, sensor):
        with (
            patch(
                "custom_components.door_occupancy.binary_sensor.async_track_state_change_event",
                return_value=lambda: None,
            ),
            patch("custom_components.door_occupancy.binary_sensor.entity_registry"),
            patch("custom_components.door_occupancy.binary_sensor.device_registry"),
        ):
            await sensor.async_added_to_hass()

        # Initial state was "off"; "off" again must not pulse.
        event = MagicMock()
        event.data = {
            "entity_id": "binary_sensor.front_door",
            "new_state": _make_state("off"),
        }
        with patch.object(sensor, "pulse") as mock_pulse:
            sensor._handle_source_event(event)

        mock_pulse.assert_not_called()
