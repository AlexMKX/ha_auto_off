"""Periodic discovery of door/lock/cover sources and creation of
corresponding occupancy binary sensors."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .binary_sensor import DoorOccupancyBinarySensor
from .const import CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)


class DoorOccupancyManager:
    """Owns per-entry discovery state for Door Occupancy."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        occupancy_timeout: float,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._occupancy_timeout = occupancy_timeout
        self._sensors: dict[str, DoorOccupancyBinarySensor] = {}
        self._async_add_entities: AddEntitiesCallback | None = None
        self._remove_listener = None

    def _find_sources(self) -> list[str]:
        entities: set[str] = set()
        for state in self.hass.states.async_all(["binary_sensor"]):
            if state.attributes.get("device_class") == "door":
                entities.add(state.entity_id)
        for state in self.hass.states.async_all(["cover", "lock"]):
            entities.add(state.entity_id)
        return sorted(entities)

    async def async_platform_ready(self, async_add_entities: AddEntitiesCallback) -> None:
        """Called once by the binary_sensor platform setup."""
        self._async_add_entities = async_add_entities
        await self._discover_and_add_sensors()
        poll_interval = self.entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        self._remove_listener = async_track_time_interval(self.hass, self._on_tick, timedelta(seconds=poll_interval))

    async def _on_tick(self, _now) -> None:
        await self._discover_and_add_sensors()

    async def _discover_and_add_sensors(self) -> None:
        if self._async_add_entities is None:
            return
        sources = self._find_sources()
        new_sensors = []
        for source_id in sources:
            if source_id in self._sensors:
                continue
            sensor = DoorOccupancyBinarySensor(
                hass=self.hass,
                source_entity_id=source_id,
                config_entry=self.entry,
                occupancy_timeout=self._occupancy_timeout,
            )
            self._sensors[source_id] = sensor
            new_sensors.append(sensor)
        if new_sensors:
            _LOGGER.info("Adding %d new occupancy sensors", len(new_sensors))
            self._async_add_entities(new_sensors, update_before_add=True)

    async def async_unload(self) -> None:
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None
