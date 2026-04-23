"""Binary sensor entity producing occupancy pulses for door-like sources.

Subscribes to state changes of the source entity (a door binary_sensor,
a lock, or a cover) and calls pulse() on every real state change, which
turns the occupancy sensor on briefly. The on-to-off reset is owned by
AutoResetBinarySensor.
"""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .auto_reset import AutoResetBinarySensor
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_IGNORED_STATES = {"unknown", "unavailable"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Hand the platform's add-entities callback to the discovery manager."""
    manager = hass.data[DOMAIN][entry.entry_id]
    await manager.async_platform_ready(async_add_entities)


class DoorOccupancyBinarySensor(AutoResetBinarySensor):
    """Occupancy sensor derived from a door/lock/cover source entity."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_icon = "mdi:motion-sensor"
    _attr_should_poll = False

    def __init__(
        self,
        hass: HomeAssistant,
        source_entity_id: str,
        config_entry: ConfigEntry,
        occupancy_timeout: float,
    ) -> None:
        super().__init__(hass, reset_timeout=occupancy_timeout)
        self._source_entity_id = source_entity_id
        self._config_entry = config_entry
        self._attr_name = f"{source_entity_id} Occupancy"
        self._attr_unique_id = f"{source_entity_id.replace('.', '_')}_occupancy"
        self._prev_state: str | None = None
        self._unsub = None

    async def async_added_to_hass(self) -> None:
        # Bind this entity's config entry to the source entity's device so
        # the occupancy sensor shows up on the same HA device card.
        ent_reg = entity_registry.async_get(self.hass)
        dev_reg = device_registry.async_get(self.hass)
        entry = ent_reg.async_get(self._source_entity_id)
        if entry and entry.device_id:
            dev_reg.async_update_device(
                entry.device_id, add_config_entry_id=self._config_entry.entry_id
            )

        # Seed _prev_state from the current source state so the first *real*
        # change fires pulse(), while startup is not treated as a change.
        current = self.hass.states.get(self._source_entity_id)
        self._prev_state = current.state if current else None

        self._unsub = async_track_state_change_event(
            self.hass, [self._source_entity_id], self._handle_source_event
        )
        _LOGGER.info(
            "DoorOccupancyBinarySensor '%s' attached to %s",
            self._attr_name,
            self._source_entity_id,
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_source_event(self, event) -> None:
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in _IGNORED_STATES:
            return
        if new_state.state == self._prev_state:
            return
        self._prev_state = new_state.state
        self.pulse()

    @property
    def device_info(self):
        ent_reg = entity_registry.async_get(self.hass)
        entry = ent_reg.async_get(self._source_entity_id)
        if not entry or not entry.device_id:
            return None
        dev_reg = device_registry.async_get(self.hass)
        device = dev_reg.async_get(entry.device_id)
        if not device:
            return None
        return DeviceInfo(identifiers=device.identifiers)

    @property
    def extra_state_attributes(self):
        return {"source_entity_id": self._source_entity_id}
