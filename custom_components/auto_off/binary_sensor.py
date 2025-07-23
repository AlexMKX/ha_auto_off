from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.helpers import entity_registry, device_registry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.config_entries import ConfigEntry
import logging
from .const import DOMAIN
from .integration_manager import async_setup_integration

_LOGGER = logging.getLogger(__name__)
OCCUPANCY_SUFFIX = "_occupancy"
OCCUPANCY_TIMEOUT = 15  # seconds

async def async_setup_entry(hass, entry, async_add_entities):
    await async_setup_integration(hass, entry, async_add_entities)

class DoorOccupancyBinarySensor(BinarySensorEntity):
    def __init__(self, hass, source_entity_id, config_entry):
        self.hass = hass
        self._source_entity_id = source_entity_id
        self._config_entry = config_entry
        self._attr_name = f"{source_entity_id} Occupancy"
        self._attr_unique_id = f"{source_entity_id.replace('.', '_')}_occupancy"
        self._attr_device_class = BinarySensorDeviceClass.OCCUPANCY
        self._attr_icon = "mdi:motion-sensor"
        self._attr_should_poll = False
        self._attr_is_on = False
        self._timer = None
        self._unsub = None
        self._prev_state = None
        _LOGGER.info(f"DoorOccupancyBinarySensor '{self._attr_name}' for '{source_entity_id}' initialized")

    async def async_added_to_hass(self):
        # Привязываем config entry к устройству

        ent_reg = entity_registry.async_get(self.hass)
        dev_reg = device_registry.async_get(self.hass)
        entry = ent_reg.async_get(self._source_entity_id)
        if entry and entry.device_id:
            dev_reg.async_update_device(
                entry.device_id,
                add_config_entry_id=self._config_entry.entry_id
            )
        self._unsub = async_track_state_change_event(
            self.hass, [self._source_entity_id], self._handle_door_event
        )
        _LOGGER.info(f'Added {self._source_entity_id} to {self._config_entry.entry_id}')

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()
        if self._timer:
            self._timer.cancel()

    @property
    def is_on(self):
        return self._attr_is_on

    @property
    def extra_state_attributes(self):
        return {
            "source_entity_id": self._source_entity_id,
        }

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
        return DeviceInfo(
            identifiers=device.identifiers,
        )

    async def _handle_door_event(self, event):
        new_state = event.data.get("new_state")
        old_state = event.data.get("old_state")
        if not new_state or not old_state:
            return
        if new_state.state.lower() in ("unknown", "unavailable"):
            return
        if self._prev_state == new_state.state:
            return
        self._prev_state = new_state.state
        self._attr_is_on = True
        self.async_write_ha_state()
        self._restart_timer()

    def _restart_timer(self):
        if self._timer:
            self._timer.cancel()
        loop = self.hass.loop
        self._timer = loop.call_later(
            OCCUPANCY_TIMEOUT, lambda: self._set_occupancy_off_callback()
        )

    def _set_occupancy_off_callback(self):
        # Для совместимости с call_later (sync wrapper)
        import asyncio
        asyncio.create_task(self._set_occupancy_off())

    async def _set_occupancy_off(self):
        self._attr_is_on = False
        self.async_write_ha_state()
        self._timer = None
