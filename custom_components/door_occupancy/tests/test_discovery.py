"""Tests for DoorOccupancyManager discovery behavior."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.door_occupancy.discovery import DoorOccupancyManager


def _fake_state(entity_id: str, device_class: str | None = None):
    state = MagicMock()
    state.entity_id = entity_id
    state.attributes = {"device_class": device_class} if device_class else {}
    return state


class TestDoorOccupancyManager:
    @pytest.fixture
    def config_entry(self):
        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.data = {"poll_interval": 30, "occupancy_timeout": 15}
        return entry

    @pytest.fixture
    def manager(self, hass, config_entry):
        return DoorOccupancyManager(hass, config_entry, occupancy_timeout=15)

    async def test_finds_door_binary_sensors_locks_and_covers(
        self, manager, hass
    ):
        def fake_all(domains):
            if domains == ["binary_sensor"]:
                return [
                    _fake_state("binary_sensor.front_door", "door"),
                    _fake_state("binary_sensor.motion", "motion"),  # skip
                ]
            if set(domains) >= {"cover", "lock"}:
                return [
                    _fake_state("lock.front"),
                    _fake_state("cover.garage"),
                ]
            return []

        hass.states.async_all.side_effect = fake_all
        found = manager._find_sources()

        assert set(found) == {
            "binary_sensor.front_door",
            "lock.front",
            "cover.garage",
        }

    async def test_discovery_adds_one_sensor_per_source(self, manager, hass):
        def fake_all(domains):
            if domains == ["binary_sensor"]:
                return [_fake_state("binary_sensor.front_door", "door")]
            return [_fake_state("lock.front")]

        hass.states.async_all.side_effect = fake_all
        add_entities = MagicMock()
        with patch(
            "custom_components.door_occupancy.discovery.async_track_time_interval",
            return_value=MagicMock(),
        ):
            await manager.async_platform_ready(add_entities)

        # One discovery tick creates entities once for each new source.
        assert add_entities.call_count == 1
        sensors = add_entities.call_args[0][0]
        assert len(sensors) == 2

    async def test_discovery_is_idempotent(self, manager, hass):
        def fake_all(domains):
            if domains == ["binary_sensor"]:
                return [_fake_state("binary_sensor.front_door", "door")]
            return []

        hass.states.async_all.side_effect = fake_all
        add_entities = MagicMock()
        with patch(
            "custom_components.door_occupancy.discovery.async_track_time_interval",
            return_value=MagicMock(),
        ):
            await manager.async_platform_ready(add_entities)

        # Running the periodic tick again must not re-add the same sensor.
        await manager._discover_and_add_sensors()
        assert add_entities.call_count == 1

    async def test_unload_cancels_periodic_listener(self, manager):
        fake_unsub = MagicMock()
        manager._remove_listener = fake_unsub

        await manager.async_unload()

        fake_unsub.assert_called_once()
        assert manager._remove_listener is None
