import logging
import asyncio
from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
from pydantic import BaseModel, field_validator, ValidationError
from typing import List, Optional, Callable, Dict
from homeassistant.helpers import entity_registry
import functools
from homeassistant.helpers.event import async_track_time_interval
from datetime import timedelta

_LOGGER = logging.getLogger(__name__)

class DoorOccupancyManager:
    """
    Manager: finds entity_ids of suitable doors/locks/covers, manages occupancy sensors, calls async_add_entities for new ones.
    """
    def __init__(self, hass: HomeAssistant, config_entry=None):
        self.hass = hass
        self.config_entry = config_entry
        self._door_entities = []  # List of entity_id
        self._occupancy_sensors: Dict[str, object] = {}  # entity_id -> sensor instance
        self._async_add_entities: Optional[Callable] = None

    async def _find_doors(self):
        entities = set()
        for state in self.hass.states.async_all(["binary_sensor"]):
            if state.attributes.get("device_class") == "door":
                entities.add(state.entity_id)
        for state in self.hass.states.async_all(['cover', 'lock']):
            entities.add(state.entity_id)
        return list(entities)

    async def periodic_discovery(self):
        try:
            await self._discover_and_add_sensors()
        except Exception as e:
            _LOGGER.error(f"Error in periodic discovery: {e}")

    async def _discover_and_add_sensors(self):
        from .binary_sensor import DoorOccupancyBinarySensor
        all_entities = await self._find_doors()
        new_sensors = []
        for eid in all_entities:
            if eid not in self._occupancy_sensors:
                sensor = DoorOccupancyBinarySensor(self.hass, eid, self.config_entry)
                self._occupancy_sensors[eid] = sensor
                new_sensors.append(sensor)
        if new_sensors and self._async_add_entities is not None:
            try:
                _LOGGER.info(f"Adding {len(new_sensors)} new sensors")
                self._async_add_entities(new_sensors, update_before_add=True)
            except Exception as e:
                _LOGGER.error(f"Error adding new sensors: {e}")
        self._door_entities = list(self._occupancy_sensors.keys())

    @property
    def door_entities(self):
        return self._door_entities

    async def async_unload(self):
        pass
