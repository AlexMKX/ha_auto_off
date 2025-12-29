import asyncio
from homeassistant.helpers.event import async_track_time_interval
from datetime import timedelta
import logging
from .auto_off import AutoOffManager, IntegrationConfig
from .door_occupancy import DoorOccupancyManager
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

def get_integration_config(hass, entry=None):
    yaml_config = hass.data.get(DOMAIN, {}).get('yaml_config')
    if yaml_config:
        return yaml_config
    if entry is not None:
        return entry.data
    raise RuntimeError("No configuration found for integration")

class IntegrationManager:
    def __init__(self, hass, config, entry, async_add_entities):
        self.hass = hass
        self.entry = entry
        self.async_add_entities = async_add_entities
        self.config_model = IntegrationConfig.model_validate(config)
        self.auto_off = AutoOffManager(hass, self.config_model.groups)
        self.door_occupancy = DoorOccupancyManager(hass, entry)
        self._lock = asyncio.Lock()
        self._remove_listener = None

    async def async_initialize(self):
        # Initialize door_occupancy with async_add_entities
        self.door_occupancy._async_add_entities = self.async_add_entities
        await self.door_occupancy._discover_and_add_sensors()
        
        interval = self.config_model.poll_interval
        self._remove_listener = async_track_time_interval(
            self.hass, self._periodic_worker, timedelta(seconds=interval)
        )
        _LOGGER.info(f"IntegrationManager initialized with poll_interval {interval}s")

    async def _periodic_worker(self, now):
        if self._lock.locked():
            _LOGGER.warning("IntegrationManager worker already running, skipping this tick")
            return
        async with self._lock:
            await self.auto_off.periodic_worker()
            await self.door_occupancy.periodic_discovery()

    async def async_unload(self):
        if self._remove_listener:
            self._remove_listener()
            self._remove_listener = None
        await self.auto_off.async_unload()
        await self.door_occupancy.async_unload()

default_manager = None

async def async_setup_integration(hass, entry, async_add_entities):
    global default_manager
    config = get_integration_config(hass, entry)
    manager = IntegrationManager(hass, config, entry, async_add_entities)
    hass.data[DOMAIN] = manager
    default_manager = manager
    await manager.async_initialize()
    return True

async def async_unload_integration(hass, entry):
    global default_manager
    manager = hass.data.pop(DOMAIN, None)
    if manager and hasattr(manager, "async_unload"):
        await manager.async_unload()
    default_manager = None
    return True 