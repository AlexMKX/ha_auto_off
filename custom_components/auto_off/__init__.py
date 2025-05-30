import logging
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.typing import ConfigType
from .auto_off import AutoOffManager
import os
import yaml

_LOGGER = logging.getLogger(__name__)

DOMAIN = "auto_off"

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the HA Switch Auto Off integration from configuration.yaml."""
    _LOGGER.info("Setting up HA Switch Auto Off integration")
    conf = config.get(DOMAIN)
    if not conf:
        _LOGGER.error("No configuration found for %s", DOMAIN)
        return False
    # Поддержка отдельного YAML-файла (опционально)
    yaml_path = conf.get("config_path")
    if yaml_path and os.path.exists(yaml_path):
        with open(yaml_path, "r") as f:
            conf = yaml.safe_load(f)
    manager = AutoOffManager(hass, conf)
    hass.data[DOMAIN] = manager
    await manager.async_initialize()
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HA Switch Auto Off from a config entry (future-proof)."""
    return True
